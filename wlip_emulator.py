# WLIP Emulator for WeeWX
# Version: 92 (WeatherLink PC: long downloads and whole history)
#
# Changes:
# - V92: Long WeatherLink PC downloads and the whole history.
#        * DMPAFT sends the page header at once (from a COUNT query capped at the
#          download size) and reads the records 100 at a time as the pages are asked
#          for. V91 read every record first: with an old WeatherLink time stamp that
#          took longer than the ~2 s WeatherLink PC waits for the header ("Function to
#          retrieve data ... timed out"), and the long read held an SQLite read lock,
#          so WeeWX's archive write failed with "database is locked" and weewxd
#          restarted its engine (dropping every client).
#        * Waits up to 5 minutes for the first page ACK: WeatherLink PC asks
#          "Transfer N pages to your PC?" after the header and sends the ACK only when
#          the user clicks OK.
#        * max_dmpaft_records is limited to 65535 pages (327675 records), the most the
#          16-bit page count in the header can hold.
#        * New option new_station_history = recent|all. 'all' gives a client with no
#          data of its own (a new WeatherLink station) the whole database from the
#          oldest record, max_dmpaft_records per download.
# - V91: WeatherLink PC support.
#        * WRD answers 16 (Vantage Pro/Pro2). It used to answer 17, which is the
#          Vantage Vue code, so WeatherLink PC reported the station as a Vue.
#        * WRD waits for its argument bytes (0x12 0x4D + CR/LF). WeatherLink PC sends
#          "WRD" and the rest in separate TCP segments; the leftover bytes used to come
#          back later as a NAK and break the next wake-up.
#        * One buffered reader per connection. Binary payloads (DMPAFT time stamp,
#          SETTIME, EEBWR, page ACKs) are read with exact lengths and timeouts from the
#          same buffer as the commands. WeatherLink PC sends the 6-byte DMPAFT time
#          stamp as 1 + 5 bytes; the old single recv(6) got 1 byte and silently gave
#          up, so WeatherLink PC retried the download forever.
#        * DMPAFT checks the time stamp CRC, waits for the ACK before every page,
#          resends a page on NAK, stops on ESC and swallows a trailing ACK.
#        * DMP (full memory dump) laid out like the console's circular archive memory.
#        * The EEPROM image is read from the real Vantage console at start-up when WeeWX
#          drives one, otherwise built from [Station] in weewx.conf.
#        * Rain is sent in bucket clicks that match the EEPROM rain collector setting
#          (0.01 in, 0.2 mm or 0.1 mm). It was always sent as 0.01 in clicks.
#        * LOOP and LOOP2 packets rebuilt from the Davis spec: LF/CR at bytes 95-96,
#          dash values for missing sensors, north wind as 360, LPS packet masks,
#          barometer trend, sunrise/sunset.
#        * HILOWS filled from the WeeWX daily summaries instead of zeros.
#        * New commands: SETTIME, BARDATA, RXCHECK (text), RXTEST, RECEIVERS, EEBWR,
#          EEWR, EERD (text), SETPER, NEWSETUP, START, STOP, LAMPS, CLR*, PUTRAIN,
#          PUTET, BAR=, DMP.
#        * Exact command matching instead of substring matching ("RXTEST" no longer
#          answers as "TEST", "SETTIME" is no longer ignored).
#        * Database lock retries use weedb.DatabaseLockedError (weewx.CannotLock does
#          not exist).
#        * debug_detail = 2 also logs transmitted bytes.
# - V89: Implemented graceful shutdown via weewx.SHUTDOWN event binding.
# - V89: Watchdog Action 2 now uses SIGTERM for a safe, systemd-friendly restart.
# - V89: Added SQLite database lock Retry-logic to DMPAFT to prevent CannotLock crashes.
# - V89: Modernized all logging to use Python 3 f-strings for better performance.
# - V89: Eliminated silent exceptions (except: pass) as per WeeWX Devnotes.
# - V89: Added TCP Keepalive to client sockets for faster dead-connection detection.
# - V88.3: FIXED port binding timeouts by multithreading the listener setup.
# - V88: Fixed EEPROM 0x2D error by clamping archive interval to 255 mins.
# - V87: Added 'max_lag_action' (Watchdog Kill Switch). Can kill WeeWX process to force systemd restart.
# - V86: Added 'max_lag_disconnect'. Forces client disconnect if WeeWX data is stale.
# - V85: Added 'startup_delay' (Soft Start) to allow USB drivers to settle.
# - V84: Implemented Smart Logging (debug_detail levels: 0=Basic, 1=Stats, 2=Raw).
# - V83: Fixed 'client_mapping' parsing crash when multiple clients are defined (list vs string).
# - V83: Updated DMPAFT logic to emulate real Davis Logger memory limits (Hardware Record Limit).
# - V83: Added 'select' to handle_loop to detect client interrupts (fixes b'LO' error).
# - V80: Added dynamic IP-to-Port mapping via config file.
# - V78: Implemented Ring Buffer to fix command dropping (CRITICAL FIX).
# - V50: Added shutDown() to prevent "Address already in use" on restarts.

import calendar
import datetime
import itertools
import logging
import math
import os
import select
import signal
import socket
import struct
import threading
import time

import weedb
import weewx
import weewx.manager
import weewx.units
from weewx.engine import StdService

try:
    import weewx.xtypes as wx_xtypes
except ImportError:  # very old WeeWX
    wx_xtypes = None

try:
    from weeutil.weeutil import TimeSpan, archiveDaySpan, archiveMonthSpan, archiveYearSpan
except ImportError:
    TimeSpan = archiveDaySpan = archiveMonthSpan = archiveYearSpan = None

try:
    from weeutil.weeutil import archiveRainYearSpan
except ImportError:
    archiveRainYearSpan = None

try:
    import weeutil.Sun as weeutil_sun
except ImportError:
    weeutil_sun = None

log = logging.getLogger(__name__)

VERSION = "92"

DB_LOCKED_ERRORS = tuple(e for e in (getattr(weedb, 'DatabaseLockedError', None),) if e)


def logdbg(msg):
    log.debug(f"WLIP Emulator: {msg}")


def loginf(msg):
    log.info(f"WLIP Emulator: {msg}")


def logerr(msg):
    log.error(f"WLIP Emulator: {msg}")


def logcrit(msg):
    log.critical(f"WLIP Emulator: {msg}")


# ---------------------------------------------------------------------------
# Davis protocol constants
# ---------------------------------------------------------------------------

ACK = b'\x06'
CANCEL = b'\x18'      # Davis answer to a bad CRC on data sent by the PC
OK_RESPONSE = b'\n\rOK\n\r'

ACK_BYTE = 0x06
NAK_BYTE = 0x21       # "NAK" in the Davis documentation
RESEND_BYTE = 0x15    # What clients really send to ask for a page again
ESC_BYTE = 0x1B
LF = 0x0A
CR = 0x0D

HARDWARE_RECORD_LIMIT = 2560     # 512 pages of 5 records in a real console
RECORDS_PER_PAGE = 5
MAX_DMPAFT_RECORDS = 65535 * RECORDS_PER_PAGE   # the page count in the header is 16 bits
FIRST_PAGE_ACK_TIMEOUT = 300.0   # seconds; the user confirms the download first
RECORD_SIZE = 52
PAGE_SIZE = 267
LOOP_SIZE = 99
EEPROM_SIZE = 4096

STATION_TYPE_VANTAGE_PRO = 16    # Vantage Pro and Vantage Pro2
STATION_TYPE_VANTAGE_VUE = 17

# EEPROM addresses (Vantage Serial Protocol, EEPROM configuration settings)
EE_LATITUDE = 0x0B
EE_LONGITUDE = 0x0D
EE_ELEVATION = 0x0F
EE_MANUAL_OR_AUTO = 0x12
EE_DAYLIGHT_SAVINGS = 0x13
EE_GMT_OFFSET = 0x14
EE_GMT_OR_ZONE = 0x16
EE_UNIT_BITS = 0x29
EE_UNIT_BITS_COMP = 0x2A
EE_SETUP_BITS = 0x2B
EE_RAIN_SEASON_START = 0x2C
EE_ARCHIVE_PERIOD = 0x2D

# A Vantage Pro2 EEPROM image (first 256 bytes) used when the real console cannot be
# read. Location, time zone, units, rain collector and archive period are filled in at
# start-up; the rest are ordinary Vantage Pro2 settings (ISS on transmitter 1, no
# calibration offsets, alarms off).
EEPROM_TEMPLATE = bytes([
    0xFF, 0x00, 0x00, 0xC7, 0xFF, 0x00, 0x00, 0xF0, 0xFF, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x17, 0x00, 0x01, 0xC8, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x0A, 0xFF, 0x0A, 0xFF, 0x0A,
    0xFF, 0x0A, 0xFF, 0x0A, 0xFF, 0x0A, 0xFF, 0x0A, 0xFF, 0x7A, 0x85, 0xDD, 0x01, 0x05, 0xF1, 0x0E,
    0xFF, 0xFF, 0x00, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03,
    0x02, 0x03, 0x00, 0x00, 0xFF, 0xFF, 0x00, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x7F, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0x07, 0x04, 0x10, 0x10, 0x0D, 0x0D, 0x04, 0x0C, 0xE1, 0xE1, 0x49, 0x00, 0x4D, 0x61, 0x55,
    0x2D, 0x29, 0x00, 0x02, 0xFF, 0x1E, 0xFF, 0xB2, 0x9E, 0xAA, 0xD2, 0xD6, 0xFF, 0x0F, 0xFF, 0xFF,
    0x0D, 0x0D, 0x0D, 0x0D, 0x0D, 0x74, 0x74, 0x74, 0x74, 0x74, 0x0C, 0x0C, 0x0C, 0x0C, 0x07, 0x0D,
    0x0D, 0x0D, 0x0D, 0x0D, 0x0C, 0x0C, 0x0C, 0x0C, 0x0C, 0x0C, 0x0C, 0x0C, 0x0C, 0x0C, 0x0C, 0x0D,
    0x0D, 0x0D, 0x0D, 0x0D, 0x0C, 0x0C, 0x0C, 0x0C, 0x0C, 0x0C, 0x0C, 0x0C, 0x0C, 0x0C, 0x0C, 0x0C,
])

CRC_TABLE = [
    0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50a5, 0x60c6, 0x70e7,
    0x8108, 0x9129, 0xa14a, 0xb16b, 0xc18c, 0xd1ad, 0xe1ce, 0xf1ef,
    0x1231, 0x0210, 0x3273, 0x2252, 0x52b5, 0x4294, 0x72f7, 0x62d6,
    0x9339, 0x8318, 0xb37b, 0xa35a, 0xd3bd, 0xc39c, 0xf3ff, 0xe3de,
    0x2462, 0x3443, 0x0420, 0x1401, 0x64e6, 0x74c7, 0x44a4, 0x5485,
    0xa56a, 0xb54b, 0x8528, 0x9509, 0xe5ee, 0xf5cf, 0xc5ac, 0xd58d,
    0x3653, 0x2672, 0x1611, 0x0630, 0x76d7, 0x66f6, 0x5695, 0x46b4,
    0xb75b, 0xa77a, 0x9719, 0x8738, 0xf7df, 0xe7fe, 0xd79d, 0xc7bc,
    0x48c4, 0x58e5, 0x6886, 0x78a7, 0x0840, 0x1861, 0x2802, 0x3823,
    0xc9cc, 0xd9ed, 0xe98e, 0xf9af, 0x8948, 0x9969, 0xa90a, 0xb92b,
    0x5af5, 0x4ad4, 0x7ab7, 0x6a96, 0x1a71, 0x0a50, 0x3a33, 0x2a12,
    0xdbfd, 0xcbdc, 0xfbbf, 0xeb9e, 0x9b79, 0x8b58, 0xbb3b, 0xab1a,
    0x6ca6, 0x7c87, 0x4ce4, 0x5cc5, 0x2c22, 0x3c03, 0x0c60, 0x1c41,
    0xedae, 0xfd8f, 0xcdec, 0xddcd, 0xad2a, 0xbd0b, 0x8d68, 0x9d49,
    0x7e97, 0x6eb6, 0x5ed5, 0x4ef4, 0x3e13, 0x2e32, 0x1e51, 0x0e70,
    0xff9f, 0xefbe, 0xdfdd, 0xcffc, 0xbf1b, 0xaf3a, 0x9f59, 0x8f78,
    0x9188, 0x81a9, 0xb1ca, 0xa1eb, 0xd10c, 0xc12d, 0xf14e, 0xe16f,
    0x1080, 0x00a1, 0x30c2, 0x20e3, 0x5004, 0x4025, 0x7046, 0x6067,
    0x83b9, 0x9398, 0xa3fb, 0xb3da, 0xc33d, 0xd31c, 0xe37f, 0xf35e,
    0x02b1, 0x1290, 0x22f3, 0x32d2, 0x4235, 0x5214, 0x6277, 0x7256,
    0xb5ea, 0xa5cb, 0x95a8, 0x8589, 0xf56e, 0xe54f, 0xd52c, 0xc50d,
    0x34e2, 0x24c3, 0x14a0, 0x0481, 0x7466, 0x6447, 0x5424, 0x4405,
    0xa7db, 0xb7fa, 0x8799, 0x97b8, 0xe75f, 0xf77e, 0xc71d, 0xd73c,
    0x26d3, 0x36f2, 0x0691, 0x16b0, 0x6657, 0x7676, 0x4615, 0x5634,
    0xd94c, 0xc96d, 0xf90e, 0xe92f, 0x99c8, 0x89e9, 0xb98a, 0xa9ab,
    0x5844, 0x4865, 0x7806, 0x6827, 0x18c0, 0x08e1, 0x3882, 0x28a3,
    0xcb7d, 0xdb5c, 0xeb3f, 0xfb1e, 0x8bf9, 0x9bd8, 0xabbb, 0xbb9a,
    0x4a75, 0x5a54, 0x6a37, 0x7a16, 0x0af1, 0x1ad0, 0x2ab3, 0x3a92,
    0xfd2e, 0xed0f, 0xdd6c, 0xcd4d, 0xbdaa, 0xad8b, 0x9de8, 0x8dc9,
    0x7c26, 0x6c07, 0x5c64, 0x4c45, 0x3ca2, 0x2c83, 0x1ce0, 0x0cc1,
    0xef1f, 0xff3e, 0xcf5d, 0xdf7c, 0xaf9b, 0xbfba, 0x8fd9, 0x9ff8,
    0x6e17, 0x7e36, 0x4e55, 0x5e74, 0x2e93, 0x3eb2, 0x0ed1, 0x1ef0
]


def crc16(data):
    crc = 0
    for byte in data:
        crc = (crc << 8) ^ CRC_TABLE[((crc >> 8) ^ byte) & 0xFF]
        crc = crc & 0xFFFF
    return crc


def with_crc(payload):
    """Davis blocks carry a big-endian CRC16 after the data."""
    return bytes(payload) + struct.pack('>H', crc16(payload))


# ---------------------------------------------------------------------------
# Value encoding helpers
# ---------------------------------------------------------------------------

def _num(value):
    """Return value as a finite float, or None."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def enc(value, scale=1.0, offset=0.0, lo=0, hi=255, dash=255):
    """Scale, round and clamp a value for a Davis field. Missing values become 'dash'.

    lo/hi should exclude the dash value, so a real reading never looks like a dash."""
    f = _num(value)
    if f is None:
        return dash
    return int(max(lo, min(hi, round(f * scale + offset))))


def enc_temp10(value, dash=32767):
    """Temperature in tenths of a degree F (signed 16 bit)."""
    return enc(value, 10.0, 0, -32767, 32766, dash)


def enc_extra_temp(value):
    """Extra/soil/leaf temperature: whole degrees F + 90 in one byte."""
    return enc(value, 1.0, 90, 0, 254, 255)


def enc_byte(value, scale=1.0):
    return enc(value, scale, 0, 0, 254, 255)


def enc_whole_f(value):
    """Dew point, heat index, wind chill, THSW: whole degrees F, 255 = dashed."""
    v = enc(value, 1.0, 0, -32768, 32767, 255)
    if v != 255 and (v & 0xFF) == 0xFF:
        v -= 1  # never look like the dash value
    return v


def enc_wind_dir_degrees(value):
    """LOOP wind direction: 1..360 degrees, 0 = no direction, 360 = north."""
    f = _num(value)
    if f is None:
        return 0
    d = int(round(f)) % 360
    return 360 if d == 0 else d


def enc_wind_dir_code(value):
    """Archive wind direction: 0..15 compass points, 255 = dashed."""
    f = _num(value)
    if f is None:
        return 255
    return int(round(f / 22.5)) % 16


def davis_hhmm(epoch):
    """Epoch time to the Davis hour*100+minute form (local time), 0xFFFF if unknown."""
    f = _num(epoch)
    if f is None or f <= 0:
        return 0xFFFF
    t = time.localtime(f)
    return t.tm_hour * 100 + t.tm_min


def davis_date(epoch):
    """Archive date stamp: day + month*32 + (year-2000)*512 (local time)."""
    t = time.localtime(epoch)
    return t.tm_mday + (t.tm_mon << 5) + ((t.tm_year - 2000) << 9)


def davis_loop_date(epoch):
    """LOOP storm start date: month<<12 | day<<7 | (year-2000), 0xFFFF if none."""
    f = _num(epoch)
    if f is None or f <= 0:
        return 0xFFFF
    t = time.localtime(f)
    return (t.tm_mon << 12) | (t.tm_mday << 7) | ((t.tm_year - 2000) & 0x7F)


def decode_davis_datetime(date_stamp, time_stamp):
    """Archive date/time stamp to epoch (local time), or None if invalid."""
    try:
        tt = (((date_stamp >> 9) & 0x7F) + 2000, (date_stamp >> 5) & 0x0F, date_stamp & 0x1F,
              time_stamp // 100, time_stamp % 100, 0, 0, 0, -1)
        if not (1 <= tt[1] <= 12 and 1 <= tt[2] <= 31 and tt[3] <= 23 and tt[4] <= 59):
            return None
        return int(time.mktime(tt))
    except (OverflowError, ValueError):
        return None


class RainEncoder(object):
    """Converts inches of rain to bucket clicks of the configured rain collector."""

    CLICKS_PER_INCH = {0: 100.0, 1: 25.4 / 0.2, 2: 25.4 / 0.1}
    NAMES = {0: '0.01 in', 1: '0.2 mm', 2: '0.1 mm'}

    def __init__(self, bucket_type):
        self.bucket_type = bucket_type if bucket_type in self.CLICKS_PER_INCH else 0
        self.per_inch = self.CLICKS_PER_INCH[self.bucket_type]
        self.name = self.NAMES[self.bucket_type]

    def clicks(self, inches, dash=0):
        return enc(inches, self.per_inch, 0, 0, 0xFFFE, dash)


def first(values, *keys):
    """First non-missing value among keys in a dict."""
    for k in keys:
        v = _num(values.get(k))
        if v is not None:
            return v
    return None


# ---------------------------------------------------------------------------
# Packet builders. 'p' is a dictionary in US units (weewx.US).
# ---------------------------------------------------------------------------

def build_loop1(p, rain, next_record=0, bar_trend=0):
    """Davis LOOP (type 0) packet, 99 bytes including LF, CR and CRC."""
    pkt = bytearray(LOOP_SIZE)
    pkt[0:3] = b'LOO'
    struct.pack_into('<b', pkt, 3, bar_trend)
    pkt[4] = 0
    struct.pack_into('<H', pkt, 5, next_record % HARDWARE_RECORD_LIMIT)
    struct.pack_into('<H', pkt, 7, enc(p.get('barometer'), 1000, 0, 1, 65535, 0))
    struct.pack_into('<h', pkt, 9, enc_temp10(p.get('inTemp')))
    pkt[11] = enc_byte(p.get('inHumidity'))
    struct.pack_into('<h', pkt, 12, enc_temp10(p.get('outTemp')))
    pkt[14] = enc_byte(p.get('windSpeed'))
    pkt[15] = enc_byte(first(p, 'windSpeed10', 'windSpeed'))
    struct.pack_into('<H', pkt, 16, enc_wind_dir_degrees(p.get('windDir')))
    for i in range(7):
        pkt[18 + i] = enc_extra_temp(p.get(f'extraTemp{i + 1}'))
    for i in range(4):
        pkt[25 + i] = enc_extra_temp(p.get(f'soilTemp{i + 1}'))
        pkt[29 + i] = enc_extra_temp(p.get(f'leafTemp{i + 1}'))
    pkt[33] = enc_byte(p.get('outHumidity'))
    for i in range(7):
        pkt[34 + i] = enc_byte(p.get(f'extraHumid{i + 1}'))
    struct.pack_into('<H', pkt, 41, rain.clicks(p.get('rainRate')))
    pkt[43] = enc_byte(p.get('UV'), 10.0)
    struct.pack_into('<H', pkt, 44, enc(p.get('radiation'), 1, 0, 0, 32766, 32767))
    struct.pack_into('<H', pkt, 46, rain.clicks(p.get('stormRain')))
    struct.pack_into('<H', pkt, 48, davis_loop_date(p.get('stormStart')))
    struct.pack_into('<H', pkt, 50, rain.clicks(p.get('dayRain')))
    struct.pack_into('<H', pkt, 52, rain.clicks(p.get('monthRain')))
    struct.pack_into('<H', pkt, 54, rain.clicks(p.get('yearRain')))
    struct.pack_into('<H', pkt, 56, enc(p.get('dayET'), 1000, 0, 0, 65535, 0))
    struct.pack_into('<H', pkt, 58, enc(p.get('monthET'), 100, 0, 0, 65535, 0))
    struct.pack_into('<H', pkt, 60, enc(p.get('yearET'), 100, 0, 0, 65535, 0))
    for i in range(4):
        pkt[62 + i] = enc_byte(p.get(f'soilMoist{i + 1}'))
        pkt[66 + i] = enc_byte(p.get(f'leafWet{i + 1}'))
    # 70..85: alarm bits, all clear
    pkt[86] = enc(p.get('txBatteryStatus'), 1, 0, 0, 255, 0)
    volts = _num(p.get('consBatteryVoltage')) or 4.7
    struct.pack_into('<H', pkt, 87, int(round(volts * 100 * 512 / 300)) & 0xFFFF)
    pkt[89] = enc(p.get('forecastIcon'), 1, 0, 0, 255, 0)
    pkt[90] = enc(p.get('forecastRule'), 1, 0, 0, 255, 0)
    struct.pack_into('<H', pkt, 91, davis_hhmm(p.get('sunrise')))
    struct.pack_into('<H', pkt, 93, davis_hhmm(p.get('sunset')))
    pkt[95] = LF
    pkt[96] = CR
    struct.pack_into('>H', pkt, 97, crc16(pkt[0:97]))
    return bytes(pkt)


def build_loop2(p, rain, bar_trend=0):
    """Davis LOOP2 (type 1) packet, 99 bytes including LF, CR and CRC."""
    pkt = bytearray(LOOP_SIZE)
    pkt[0:3] = b'LOO'
    struct.pack_into('<b', pkt, 3, bar_trend)
    pkt[4] = 1
    struct.pack_into('<H', pkt, 5, 0x7FFF)
    struct.pack_into('<H', pkt, 7, enc(p.get('barometer'), 1000, 0, 1, 65535, 0))
    struct.pack_into('<h', pkt, 9, enc_temp10(p.get('inTemp')))
    pkt[11] = enc_byte(p.get('inHumidity'))
    struct.pack_into('<h', pkt, 12, enc_temp10(p.get('outTemp')))
    pkt[14] = enc_byte(p.get('windSpeed'))
    pkt[15] = 0xFF
    struct.pack_into('<H', pkt, 16, enc_wind_dir_degrees(p.get('windDir')))
    struct.pack_into('<H', pkt, 18, enc(first(p, 'windSpeed10', 'windSpeed'), 10, 0, 0, 0xFFFE, 0xFFFF))
    struct.pack_into('<H', pkt, 20, enc(first(p, 'windSpeed2', 'windSpeed'), 10, 0, 0, 0xFFFE, 0xFFFF))
    struct.pack_into('<H', pkt, 22, enc(first(p, 'windGust10', 'windGust', 'windSpeed'), 1, 0, 0, 254, 255))
    struct.pack_into('<H', pkt, 24,
                     enc_wind_dir_degrees(first(p, 'windGustDir10', 'windGustDir', 'windDir')))
    struct.pack_into('<H', pkt, 26, 0x7FFF)
    struct.pack_into('<H', pkt, 28, 0x7FFF)
    struct.pack_into('<h', pkt, 30, enc_whole_f(p.get('dewpoint')))
    pkt[32] = 0xFF
    pkt[33] = enc_byte(p.get('outHumidity'))
    pkt[34] = 0xFF
    struct.pack_into('<h', pkt, 35, enc_whole_f(p.get('heatindex')))
    struct.pack_into('<h', pkt, 37, enc_whole_f(p.get('windchill')))
    struct.pack_into('<h', pkt, 39, enc_whole_f(p.get('THSW')))
    struct.pack_into('<H', pkt, 41, rain.clicks(p.get('rainRate')))
    pkt[43] = enc_byte(p.get('UV'), 10.0)
    struct.pack_into('<H', pkt, 44, enc(p.get('radiation'), 1, 0, 0, 32766, 32767))
    struct.pack_into('<H', pkt, 46, rain.clicks(p.get('stormRain')))
    struct.pack_into('<H', pkt, 48, davis_loop_date(p.get('stormStart')))
    struct.pack_into('<H', pkt, 50, rain.clicks(p.get('dayRain')))
    struct.pack_into('<H', pkt, 52, rain.clicks(p.get('rain15')))
    struct.pack_into('<H', pkt, 54, rain.clicks(p.get('hourRain')))
    struct.pack_into('<H', pkt, 56, enc(p.get('dayET'), 1000, 0, 0, 65535, 0))
    struct.pack_into('<H', pkt, 58, rain.clicks(p.get('rain24')))
    pkt[60] = enc(p.get('bar_reduction'), 1, 0, 0, 255, 2)
    struct.pack_into('<h', pkt, 61, enc(p.get('bar_offset'), 1000, 0, -32768, 32767, 0))
    struct.pack_into('<h', pkt, 63, enc(p.get('bar_calibration'), 1000, 0, -32768, 32767, 0))
    struct.pack_into('<H', pkt, 65, enc(first(p, 'pressure_raw', 'pressure'), 1000, 0, 1, 65535, 0))
    struct.pack_into('<H', pkt, 67, enc(p.get('pressure'), 1000, 0, 1, 65535, 0))
    struct.pack_into('<H', pkt, 69, enc(p.get('altimeter'), 1000, 0, 1, 65535, 0))
    pkt[71] = 0xFF
    pkt[72] = 0xFF
    # 73..82: graph pointers, left at 0
    for off in range(83, 95, 2):
        struct.pack_into('<H', pkt, off, 0x7FFF)
    pkt[95] = LF
    pkt[96] = CR
    struct.pack_into('>H', pkt, 97, crc16(pkt[0:97]))
    return bytes(pkt)


def expected_wind_samples(interval_min, iss_id=1):
    """Wind samples a Vantage Pro2 expects per archive interval (Davis formula)."""
    return 960.0 * interval_min / float(41 + iss_id - 1)


def build_archive_record(rec, rain, default_interval=5):
    """Davis Rev B archive record (52 bytes). 'rec' is a WeeWX archive record in US units."""
    ts = int(rec['dateTime'])
    interval = int(_num(rec.get('interval')) or default_interval)
    r = bytearray(RECORD_SIZE)
    struct.pack_into('<H', r, 0, davis_date(ts))
    t = time.localtime(ts)
    struct.pack_into('<H', r, 2, t.tm_hour * 100 + t.tm_min)
    struct.pack_into('<h', r, 4, enc_temp10(rec.get('outTemp')))
    struct.pack_into('<h', r, 6, enc_temp10(first(rec, 'highOutTemp', 'outTemp'), dash=-32768))
    struct.pack_into('<h', r, 8, enc_temp10(first(rec, 'lowOutTemp', 'outTemp')))
    struct.pack_into('<H', r, 10, rain.clicks(rec.get('rain')))
    struct.pack_into('<H', r, 12, rain.clicks(rec.get('rainRate')))
    struct.pack_into('<H', r, 14, enc(rec.get('barometer'), 1000, 0, 1, 65535, 0))
    struct.pack_into('<H', r, 16, enc(rec.get('radiation'), 1, 0, 0, 32766, 32767))
    expected = expected_wind_samples(interval)
    rx = _num(rec.get('rxCheckPercent'))
    samples = expected if rx is None else expected * max(0.0, min(100.0, rx)) / 100.0
    struct.pack_into('<H', r, 18, int(round(samples)))
    struct.pack_into('<h', r, 20, enc_temp10(rec.get('inTemp')))
    r[22] = enc_byte(rec.get('inHumidity'))
    r[23] = enc_byte(rec.get('outHumidity'))
    r[24] = enc_byte(rec.get('windSpeed'))
    r[25] = enc(first(rec, 'windGust', 'windSpeed'), 1, 0, 0, 255, 0)
    r[26] = enc_wind_dir_code(first(rec, 'windGustDir', 'windDir'))
    r[27] = enc_wind_dir_code(rec.get('windDir'))
    r[28] = enc_byte(rec.get('UV'), 10.0)
    r[29] = enc(rec.get('ET'), 1000, 0, 0, 255, 0)
    struct.pack_into('<H', r, 30, enc(first(rec, 'highRadiation', 'radiation'), 1, 0, 0, 32766, 32767))
    r[32] = enc_byte(first(rec, 'highUV', 'UV'), 10.0)
    r[33] = enc(rec.get('forecastRule'), 1, 0, 0, 192, 193)
    r[34] = enc_extra_temp(rec.get('leafTemp1'))
    r[35] = enc_extra_temp(rec.get('leafTemp2'))
    r[36] = enc_byte(rec.get('leafWet1'))
    r[37] = enc_byte(rec.get('leafWet2'))
    for i in range(4):
        r[38 + i] = enc_extra_temp(rec.get(f'soilTemp{i + 1}'))
    r[42] = 0x00  # Rev B record
    r[43] = enc_byte(rec.get('extraHumid1'))
    r[44] = enc_byte(rec.get('extraHumid2'))
    for i in range(3):
        r[45 + i] = enc_extra_temp(rec.get(f'extraTemp{i + 1}'))
    for i in range(4):
        r[48 + i] = enc_byte(rec.get(f'soilMoist{i + 1}'))
    return bytes(r)


def build_page(seq, records):
    """One 267-byte archive page: sequence byte, 5 records (0xFF filled), 4 unused, CRC."""
    page = bytearray([seq & 0xFF])
    for i in range(RECORDS_PER_PAGE):
        rec = records[i] if i < len(records) else None
        page.extend(rec if rec is not None else b'\xff' * RECORD_SIZE)
    page.extend(b'\xff' * 4)
    return with_crc(page)


def build_hilows(h, rain):
    """436-byte HILOWS block plus CRC. 'h' maps names to US-unit values and epoch times."""
    b = bytearray(436)

    def put(off, fmt, val):
        struct.pack_into(fmt, b, off, val)

    def tval(name):
        return davis_hhmm(h.get(name))

    def bar(k):
        return enc(h.get(k), 1000, 0, 1, 65535, 0)

    def hi_t(k):
        return enc_temp10(h.get(k), dash=-32768)

    def lo_t(k):
        return enc_temp10(h.get(k))

    def solar(k):
        return enc(h.get(k), 1, 0, 0, 32766, 32767)

    # Barometer
    put(0, '<H', bar('barometer_day_min'))
    put(2, '<H', bar('barometer_day_max'))
    put(4, '<H', bar('barometer_month_min'))
    put(6, '<H', bar('barometer_month_max'))
    put(8, '<H', bar('barometer_year_min'))
    put(10, '<H', bar('barometer_year_max'))
    put(12, '<H', tval('barometer_day_mintime'))
    put(14, '<H', tval('barometer_day_maxtime'))
    # Wind speed
    b[16] = enc_byte(h.get('wind_day_max'))
    put(17, '<H', tval('wind_day_maxtime'))
    b[19] = enc_byte(h.get('wind_month_max'))
    b[20] = enc_byte(h.get('wind_year_max'))
    # Inside temperature
    put(21, '<h', hi_t('inTemp_day_max'))
    put(23, '<h', lo_t('inTemp_day_min'))
    put(25, '<H', tval('inTemp_day_maxtime'))
    put(27, '<H', tval('inTemp_day_mintime'))
    put(29, '<h', lo_t('inTemp_month_min'))
    put(31, '<h', hi_t('inTemp_month_max'))
    put(33, '<h', lo_t('inTemp_year_min'))
    put(35, '<h', hi_t('inTemp_year_max'))
    # Inside humidity
    b[37] = enc_byte(h.get('inHumidity_day_max'))
    b[38] = enc_byte(h.get('inHumidity_day_min'))
    put(39, '<H', tval('inHumidity_day_maxtime'))
    put(41, '<H', tval('inHumidity_day_mintime'))
    b[43] = enc_byte(h.get('inHumidity_month_max'))
    b[44] = enc_byte(h.get('inHumidity_month_min'))
    b[45] = enc_byte(h.get('inHumidity_year_max'))
    b[46] = enc_byte(h.get('inHumidity_year_min'))
    # Outside temperature
    put(47, '<h', lo_t('outTemp_day_min'))
    put(49, '<h', hi_t('outTemp_day_max'))
    put(51, '<H', tval('outTemp_day_mintime'))
    put(53, '<H', tval('outTemp_day_maxtime'))
    put(55, '<h', hi_t('outTemp_month_max'))
    put(57, '<h', lo_t('outTemp_month_min'))
    put(59, '<h', hi_t('outTemp_year_max'))
    put(61, '<h', lo_t('outTemp_year_min'))
    # Dew point (whole degrees)
    put(63, '<h', enc_whole_f(h.get('dewpoint_day_min')))
    put(65, '<h', enc_whole_f(h.get('dewpoint_day_max')))
    put(67, '<H', tval('dewpoint_day_mintime'))
    put(69, '<H', tval('dewpoint_day_maxtime'))
    put(71, '<h', enc_whole_f(h.get('dewpoint_month_max')))
    put(73, '<h', enc_whole_f(h.get('dewpoint_month_min')))
    put(75, '<h', enc_whole_f(h.get('dewpoint_year_max')))
    put(77, '<h', enc_whole_f(h.get('dewpoint_year_min')))
    # Wind chill (lows)
    put(79, '<h', enc_whole_f(h.get('windchill_day_min')))
    put(81, '<H', tval('windchill_day_mintime'))
    put(83, '<h', enc_whole_f(h.get('windchill_month_min')))
    put(85, '<h', enc_whole_f(h.get('windchill_year_min')))
    # Heat index (highs)
    put(87, '<h', enc_whole_f(h.get('heatindex_day_max')))
    put(89, '<H', tval('heatindex_day_maxtime'))
    put(91, '<h', enc_whole_f(h.get('heatindex_month_max')))
    put(93, '<h', enc_whole_f(h.get('heatindex_year_max')))
    # THSW (highs)
    put(95, '<h', enc_whole_f(h.get('THSW_day_max')))
    put(97, '<H', tval('THSW_day_maxtime'))
    put(99, '<h', enc_whole_f(h.get('THSW_month_max')))
    put(101, '<h', enc_whole_f(h.get('THSW_year_max')))
    # Solar radiation (highs)
    put(103, '<H', solar('radiation_day_max'))
    put(105, '<H', tval('radiation_day_maxtime'))
    put(107, '<H', solar('radiation_month_max'))
    put(109, '<H', solar('radiation_year_max'))
    # UV (highs)
    b[111] = enc_byte(h.get('UV_day_max'), 10.0)
    put(112, '<H', tval('UV_day_maxtime'))
    b[114] = enc_byte(h.get('UV_month_max'), 10.0)
    b[115] = enc_byte(h.get('UV_year_max'), 10.0)
    # Rain rate (highs)
    put(116, '<H', rain.clicks(h.get('rainRate_day_max')))
    put(118, '<H', tval('rainRate_day_maxtime'))
    put(120, '<H', rain.clicks(h.get('rainRate_hour_max')))
    put(122, '<H', rain.clicks(h.get('rainRate_month_max')))
    put(124, '<H', rain.clicks(h.get('rainRate_year_max')))
    # Extra/soil/leaf temperatures, extra humidities, soil moisture, leaf wetness:
    # not tracked, sent as dashes.
    b[126:436] = b'\xff' * (436 - 126)
    return with_crc(b)


# ---------------------------------------------------------------------------
# Station statistics from the WeeWX database (fallbacks and HILOWS)
# ---------------------------------------------------------------------------

class StationStats(object):
    """Values a Davis console keeps itself but a WeeWX LOOP packet may lack.

    Refreshed from the archive database at most once per 'max_age' seconds, and on the
    next request after a new archive record."""

    HILOW_SPECS = [
        # (obs_type, aggregates for today, aggregates for this month and year)
        ('barometer', ('min', 'max', 'mintime', 'maxtime'), ('min', 'max')),
        ('inTemp', ('min', 'max', 'mintime', 'maxtime'), ('min', 'max')),
        ('inHumidity', ('min', 'max', 'mintime', 'maxtime'), ('min', 'max')),
        ('outTemp', ('min', 'max', 'mintime', 'maxtime'), ('min', 'max')),
        ('dewpoint', ('min', 'max', 'mintime', 'maxtime'), ('min', 'max')),
        ('windchill', ('min', 'mintime'), ('min',)),
        ('heatindex', ('max', 'maxtime'), ('max',)),
        ('THSW', ('max', 'maxtime'), ('max',)),
        ('radiation', ('max', 'maxtime'), ('max',)),
        ('UV', ('max', 'maxtime'), ('max',)),
        ('rainRate', ('max', 'maxtime'), ('max',)),
    ]

    def __init__(self, config_dict, binding, rain_season_start=1, max_age=60):
        self.config_dict = config_dict
        self.binding = binding
        self.rain_season_start = rain_season_start
        self.max_age = max_age
        self.lock = threading.Lock()
        self.values = {}
        self.hilows = {}
        self.updated = 0
        self.dirty = True
        self.last_archive_ts = None
        self.archive_interval_min = None

    def mark_dirty(self):
        self.dirty = True

    def get(self, now=None):
        now = now or time.time()
        with self.lock:
            if self.dirty or now - self.updated > self.max_age:
                try:
                    self._refresh(now)
                except Exception as e:
                    logerr(f"Could not read statistics from the database: {e}")
                self.updated = now
                self.dirty = False
            return dict(self.values), dict(self.hilows)

    @staticmethod
    def _agg(dbm, obs_type, span, agg):
        if wx_xtypes is None:
            return None
        try:
            vt = wx_xtypes.get_aggregate(obs_type, span, agg, dbm)
        except (weewx.UnknownType, weewx.UnknownAggregation):
            return None
        except Exception as e:
            logdbg(f"Aggregate {agg}({obs_type}) failed: {e}")
            return None
        if vt is None or vt[0] is None:
            return None
        if agg in ('mintime', 'maxtime', 'count'):
            return vt[0]
        try:
            return weewx.units.convertStd(vt, weewx.US)[0]
        except Exception:
            return vt[0]

    def _refresh(self, now):
        values = {}
        hilows = {}
        with weewx.manager.open_manager_with_config(self.config_dict, self.binding) as dbm:
            last_ts = dbm.lastGoodStamp()
            self.last_archive_ts = last_ts
            if last_ts:
                rec = dbm.getRecord(last_ts)
                if rec and rec.get('interval'):
                    self.archive_interval_min = int(rec['interval'])
            if TimeSpan is not None:
                day = archiveDaySpan(now)
                month = archiveMonthSpan(now)
                year = archiveYearSpan(now)
                if archiveRainYearSpan:
                    rain_year = archiveRainYearSpan(now, self.rain_season_start)
                else:
                    rain_year = year
                values['dayRain'] = self._agg(dbm, 'rain', day, 'sum')
                values['monthRain'] = self._agg(dbm, 'rain', month, 'sum')
                values['yearRain'] = self._agg(dbm, 'rain', rain_year, 'sum')
                values['dayET'] = self._agg(dbm, 'ET', day, 'sum')
                values['monthET'] = self._agg(dbm, 'ET', month, 'sum')
                values['yearET'] = self._agg(dbm, 'ET', year, 'sum')
                values['rain15'] = self._agg(dbm, 'rain', TimeSpan(now - 900, now), 'sum')
                values['hourRain'] = self._agg(dbm, 'rain', TimeSpan(now - 3600, now), 'sum')
                values['rain24'] = self._agg(dbm, 'rain', TimeSpan(now - 86400, now), 'sum')
                for obs, day_aggs, long_aggs in self.HILOW_SPECS:
                    for agg in day_aggs:
                        hilows[f'{obs}_day_{agg}'] = self._agg(dbm, obs, day, agg)
                    for agg in long_aggs:
                        hilows[f'{obs}_month_{agg}'] = self._agg(dbm, obs, month, agg)
                        hilows[f'{obs}_year_{agg}'] = self._agg(dbm, obs, year, agg)
                # The Davis "high wind speed" is the highest gust.
                wind_obs = 'windGust' if 'windGust' in getattr(dbm, 'daykeys', ()) else 'windSpeed'
                hilows['wind_day_max'] = self._agg(dbm, wind_obs, day, 'max')
                hilows['wind_day_maxtime'] = self._agg(dbm, wind_obs, day, 'maxtime')
                hilows['wind_month_max'] = self._agg(dbm, wind_obs, month, 'max')
                hilows['wind_year_max'] = self._agg(dbm, wind_obs, year, 'max')
                hilows['rainRate_hour_max'] = self._agg(dbm, 'rainRate', TimeSpan(now - 3600, now), 'max')
            # Barometer three hours ago, for the trend arrow.
            old = dbm.getRecord(int(now - 10800), max_delta=1800)
            if old and old.get('barometer') is not None:
                values['barometer_3h_ago'] = weewx.units.to_std_system(old, weewx.US).get('barometer')
        self.values = values
        self.hilows = hilows


# ---------------------------------------------------------------------------
# Per-connection protocol handler
# ---------------------------------------------------------------------------

class ProtocolTimeout(Exception):
    pass


class ClientSession(object):
    """Talks the Davis serial protocol with one TCP client.

    All input goes through one buffer, so bytes that arrive together with a command, or
    split across TCP segments, are never lost between command parsing and binary reads."""

    TEXT_OK_COMMANDS = {'START', 'STOP', 'LAMPS', 'RXTEST', 'GAIN'}
    ACK_ONLY_COMMANDS = {'NEWSETUP', 'CLRLOG', 'CLRALM', 'CLRCAL', 'CLRGRA', 'CLRVAR',
                         'CLRHIGHS', 'CLRLOWS', 'CLRBITS', 'CLRDATA', 'PUTRAIN', 'PUTET',
                         'SETPER'}
    LOGGED_ACK_COMMANDS = ACK_ONLY_COMMANDS - {'NEWSETUP'}

    def __init__(self, service, sock, addr):
        self.svc = service
        self.sock = sock
        self.peer = f"{addr[0]}:{addr[1]}"
        self.buf = bytearray()

    # ---- low level I/O ---------------------------------------------------

    def _fill(self, timeout):
        """Wait up to 'timeout' seconds for more input. True if bytes were added."""
        ready, _, _ = select.select([self.sock], [], [], max(0.0, timeout))
        if not ready:
            return False
        data = self.sock.recv(4096)
        if not data:
            raise ConnectionError("closed by peer")
        if self.svc.debug_detail >= 2:
            logdbg(f"RX [{self.peer}]: {data.hex(' ')}")
        self.buf.extend(data)
        return True

    def _wait_for(self, count, timeout):
        """Wait until the buffer holds at least 'count' bytes. False on timeout."""
        deadline = time.monotonic() + timeout
        while len(self.buf) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self._fill(remaining)
        return True

    def read_exact(self, count, timeout):
        if not self._wait_for(count, timeout):
            raise ProtocolTimeout(f"wanted {count} bytes, got {len(self.buf)}")
        data = bytes(self.buf[:count])
        del self.buf[:count]
        return data

    def peek_byte(self, timeout):
        if not self.buf:
            self._wait_for(1, timeout)
        return self.buf[0] if self.buf else None

    def send(self, data):
        if self.svc.debug_detail >= 2:
            shown = bytes(data[:64]).hex(' ')
            more = "" if len(data) <= 64 else f" ... ({len(data)} bytes)"
            logdbg(f"TX [{self.peer}]: {shown}{more}")
        self.sock.sendall(data)

    # ---- main loop -------------------------------------------------------

    def run(self):
        while self.svc.running:
            if not self.buf:
                self._fill(1.0)
                continue
            first_byte = self.buf[0]
            if first_byte in (LF, CR):
                # Wake-up: a console answers every LF/CR with LF CR.
                del self.buf[0]
                self.send(b'\n\r')
                continue
            if first_byte < 0x20 or first_byte >= 0x7F:
                # Stray ACK/NAK/ESC or other junk between commands.
                del self.buf[0]
                continue
            if self.buf.startswith(b'WRD'):
                self.handle_wrd()
                continue
            line = self._take_line()
            if line is None:
                if len(self.buf) > 256:
                    logdbg(f"Discarding {len(self.buf)} unterminated bytes from {self.peer}")
                    self.buf.clear()
                else:
                    self._fill(1.0)
                continue
            self.dispatch(line)

    def _take_line(self):
        """Remove and return one command line (without its LF/CR), or None if incomplete."""
        ends = [i for i in (self.buf.find(b'\n'), self.buf.find(b'\r')) if i >= 0]
        if not ends:
            return None
        end = min(ends)
        line = bytes(self.buf[:end])
        consumed = end + 1
        if self.buf[end] == CR and len(self.buf) > consumed and self.buf[consumed] == LF:
            consumed += 1  # CR LF counts as one terminator
        del self.buf[:consumed]
        return line

    def handle_wrd(self):
        """WRD 0x12 0x4D <CR|LF>: station type. WeatherLink PC sends 'WRD' and the rest
        in separate TCP segments, so wait briefly for the whole command."""
        if not (len(self.buf) > 3 and self.buf[3] in (LF, CR)):
            self._wait_for(6, 1.0)
        consumed = 3
        if self.buf[3:5] == b'\x12\x4d':
            consumed = 5
        if len(self.buf) > consumed and self.buf[consumed] in (LF, CR):
            consumed += 1
            if self.buf[consumed - 1] == CR and len(self.buf) > consumed and self.buf[consumed] == LF:
                consumed += 1
        del self.buf[:consumed]
        if self.svc.debug_detail >= 1:
            logdbg(f"WRD from {self.peer}: station type {self.svc.station_type}")
        self.send(bytes([ACK_BYTE, self.svc.station_type]))

    # ---- command dispatch -----------------------------------------------

    def dispatch(self, line):
        text = line.decode('ascii', errors='replace').strip()
        if not text:
            return
        parts = text.split()
        cmd = parts[0].upper()
        args = parts[1:]
        if self.svc.debug_detail >= 1:
            logdbg(f"Command from {self.peer}: {text}")
        try:
            if cmd.startswith('WRD'):
                self.send(bytes([ACK_BYTE, self.svc.station_type]))
            elif cmd == 'TEST':
                self.send(b'\n\rTEST\n\r')
            elif cmd == 'VER':
                self.send(OK_RESPONSE + self.svc.firmware_date.encode('ascii') + b'\n\r')
            elif cmd == 'NVER':
                self.send(OK_RESPONSE + self.svc.firmware_version.encode('ascii') + b'\n\r')
            elif cmd == 'RXCHECK':
                self.send(OK_RESPONSE + self.svc.rxcheck_text().encode('ascii') + b'\n\r')
            elif cmd == 'RECEIVERS':
                self.send(OK_RESPONSE + b'\x01')
            elif cmd == 'BARDATA':
                self.send(OK_RESPONSE + self.svc.bardata_text().encode('ascii'))
            elif cmd.startswith('BAR='):
                loginf(f"{self.peer} sent '{text}' (barometer calibration not changed)")
                self.send(OK_RESPONSE)
            elif cmd in self.TEXT_OK_COMMANDS:
                self.send(OK_RESPONSE)
            elif cmd in self.ACK_ONLY_COMMANDS:
                if cmd in self.LOGGED_ACK_COMMANDS:
                    loginf(f"{self.peer} sent '{text}' (acknowledged, nothing changed)")
                self.send(ACK)
            elif cmd == 'GETTIME':
                self.handle_gettime()
            elif cmd == 'SETTIME':
                self.handle_settime()
            elif cmd == 'EEBRD':
                self.handle_eebrd(args)
            elif cmd == 'EERD':
                self.handle_eerd(args)
            elif cmd == 'EEBWR':
                self.handle_eebwr(args)
            elif cmd == 'EEWR':
                self.handle_eewr(args)
            elif cmd == 'LOOP':
                self.handle_loop([1], self._int_arg(args, 0, 1))
            elif cmd == 'LPS':
                mask = (self._int_arg(args, 0, 1) & 0x03) or 1
                kinds = {1: [1], 2: [2], 3: [1, 2]}[mask]
                self.handle_loop(kinds, self._int_arg(args, 1, 1))
            elif cmd == 'HILOWS':
                self.send(ACK + build_hilows(self.svc.hilows(), self.svc.rain_encoder()))
            elif cmd == 'DMPAFT':
                self.handle_dmpaft()
            elif cmd == 'DMP':
                self.handle_dmp()
            else:
                loginf(f"Unsupported command from {self.peer}: {text!r}")
        except ProtocolTimeout as e:
            logdbg(f"{cmd} from {self.peer} timed out: {e}")

    @staticmethod
    def _int_arg(args, index, default):
        try:
            value = int(args[index])
        except (IndexError, ValueError):
            return default
        return value if value > 0 else default

    @staticmethod
    def _hex_args(args):
        if len(args) < 2:
            raise ValueError("needs address and length")
        return int(args[0], 16), int(args[1], 16)

    # ---- time ------------------------------------------------------------

    def handle_gettime(self):
        now = time.localtime()
        payload = bytes([now.tm_sec, now.tm_min, now.tm_hour, now.tm_mday, now.tm_mon,
                         now.tm_year - 1900])
        self.send(ACK + with_crc(payload))

    def handle_settime(self):
        self.send(ACK)
        data = self.read_exact(8, self.svc.protocol_timeout)
        if crc16(data) != 0:
            self.send(CANCEL)
            return
        sec, minute, hour, day, month, year = data[:6]
        loginf(f"{self.peer} set the console time to {year + 1900:04d}-{month:02d}-{day:02d} "
               f"{hour:02d}:{minute:02d}:{sec:02d} (ignored; the WeeWX host keeps the time)")
        self.send(ACK)

    # ---- EEPROM ----------------------------------------------------------

    def handle_eebrd(self, args):
        try:
            address, length = self._hex_args(args)
        except ValueError:
            logdbg(f"Bad EEBRD arguments from {self.peer}: {args}")
            return
        self.send(ACK + with_crc(self.svc.eeprom_read(address, length)))

    def handle_eerd(self, args):
        try:
            address, length = self._hex_args(args)
        except ValueError:
            return
        data = self.svc.eeprom_read(address, length)
        self.send(OK_RESPONSE + b''.join(b'%02X\n\r' % v for v in data))

    def handle_eebwr(self, args):
        try:
            address, length = self._hex_args(args)
        except ValueError:
            return
        self.send(ACK)
        data = self.read_exact(length + 2, self.svc.protocol_timeout)
        if crc16(data) != 0:
            self.send(CANCEL)
            return
        self.svc.eeprom_write(address, data[:-2], self.peer)
        self.send(ACK)

    def handle_eewr(self, args):
        try:
            address, value = self._hex_args(args)
        except ValueError:
            return
        self.svc.eeprom_write(address, bytes([value & 0xFF]), self.peer)
        self.send(OK_RESPONSE)

    # ---- LOOP ------------------------------------------------------------

    def handle_loop(self, kinds, count):
        if not self.svc.watchdog_ok(self):
            return
        self.send(ACK)
        for i in range(count):
            if not self.svc.running:
                break
            self.send(self.svc.loop_packet(kinds[i % len(kinds)]))
            if i == count - 1:
                break
            # A console sends one packet every 2 seconds and stops as soon as the PC
            # sends anything (normally a wake-up LF).
            if self.buf or self._wait_for(1, self.svc.loop_interval):
                if self.svc.debug_detail >= 1:
                    logdbg(f"LOOP stream to {self.peer} interrupted after {i + 1} packets")
                break

    # ---- archive download ------------------------------------------------

    def handle_dmpaft(self):
        self.send(ACK)
        data = self.read_exact(6, self.svc.protocol_timeout)
        if crc16(data) != 0:
            logdbg(f"DMPAFT time stamp from {self.peer} failed CRC: {data.hex(' ')}")
            self.send(CANCEL)
            return
        date_stamp, time_stamp = struct.unpack('<HH', data[:4])
        since = None
        if date_stamp or time_stamp:
            since = decode_davis_datetime(date_stamp, time_stamp)
            if since is None:
                logdbg(f"Undecodable DMPAFT time stamp {date_stamp:#06x} {time_stamp:#06x}")
        self.send(ACK)

        # WeatherLink PC gives up if the page header takes more than about 2 seconds, so
        # count the records first and read them only as the pages are asked for.
        started = time.monotonic()
        whole_history = since is None and self.svc.new_station_history == 'all'
        after, count = self.svc.plan_download(since, whole_history=whole_history)
        npages = (count + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE
        if since:
            when = time.strftime('%Y-%m-%d %H:%M', time.localtime(since))
        else:
            when = 'the start (whole history)' if whole_history else 'no time stamp'
        loginf(f"DMPAFT from {self.peer}: {count} records after {when}, {npages} pages "
               f"(counted in {time.monotonic() - started:.2f} s)")
        self.send(with_crc(struct.pack('<HH', npages, 0)))
        records = self.svc.iter_archive_records(after, count)
        self._send_pages(npages, lambda n: list(itertools.islice(records, RECORDS_PER_PAGE)))
        if npages:
            logdbg(f"DMPAFT to {self.peer} ended after {time.monotonic() - started:.1f} s")

    def handle_dmp(self):
        self.send(ACK)
        slots = self.svc.archive_memory_image()
        loginf(f"DMP from {self.peer}: {sum(1 for s in slots if s)} records in 512 pages")
        self._send_pages(512, lambda n: slots[n * RECORDS_PER_PAGE:(n + 1) * RECORDS_PER_PAGE],
                         first_ack_optional=True)

    def _send_pages(self, npages, records_for_page, first_ack_optional=False):
        """Send archive pages. The client sends ACK for the next page, NAK (0x21 or
        0x15) to get the same page again, and ESC to stop."""
        page_no = 0
        page = None
        while page_no < npages and self.svc.running:
            optional = first_ack_optional and page is None
            if optional:
                wait = 0.5
            elif page is None:
                # WeatherLink PC asks "Transfer N pages to your PC?" after the header and
                # sends the first ACK only when the user clicks OK.
                wait = max(self.svc.protocol_timeout, FIRST_PAGE_ACK_TIMEOUT)
            else:
                wait = self.svc.protocol_timeout
            reply = self.peek_byte(wait)
            if reply is None:
                if not optional:
                    logdbg(f"Archive download to {self.peer} timed out at page {page_no}/{npages}")
                    return
                reply = ACK_BYTE
            else:
                del self.buf[0]
            if reply == ACK_BYTE:
                if page is not None:
                    page_no += 1
                    if page_no >= npages:
                        return
                page = build_page(page_no, records_for_page(page_no))
                self.send(page)
            elif reply in (NAK_BYTE, RESEND_BYTE) and page is not None:
                logdbg(f"{self.peer} asked for page {page_no} again")
                self.send(page)
            elif reply == ESC_BYTE:
                logdbg(f"{self.peer} cancelled the archive download at page {page_no}")
                return
            else:
                # Anything else (e.g. a wake-up LF) ends the download; leave it for
                # the main loop.
                self.buf.insert(0, reply)
                logdbg(f"Archive download to {self.peer} ended by byte {reply:#04x}")
                return
        # Some clients ACK the last page, others go straight to the next command.
        if self.peek_byte(0.3) == ACK_BYTE:
            del self.buf[0]


# ---------------------------------------------------------------------------
# The WeeWX service
# ---------------------------------------------------------------------------

class WeatherLinkEmulator(StdService):
    def __init__(self, engine, config_dict):
        super(WeatherLinkEmulator, self).__init__(engine, config_dict)
        options = config_dict.get('WeatherLinkEmulator', {})
        self.port = int(options.get('port', 22222))
        self.max_clients = int(options.get('max_clients', 10))

        # LOGGING & DEBUG (0=Basic, 1=Stats/Lag, 2=Raw Hex)
        self.debug_detail = int(options.get('debug_detail', 0))

        # SOFT START & WATCHDOG
        self.startup_delay = int(options.get('startup_delay', 0))
        self.max_lag_threshold = int(options.get('max_lag_threshold', 0))
        self.max_lag_action = int(options.get('max_lag_action', 0))  # 0=Log, 1=Disconnect, 2=KillWeeWX

        # Protocol
        self.station_type = int(options.get('station_type', STATION_TYPE_VANTAGE_PRO))
        self.firmware_date = ' '.join(str(options.get('firmware_date', 'May 1 2012')).split())
        if len(self.firmware_date.split()) == 3 and len(self.firmware_date.split()[1]) == 1:
            m, d, y = self.firmware_date.split()
            self.firmware_date = f"{m}  {d} {y}"   # C __DATE__ style: "May  1 2012"
        self.firmware_version = str(options.get('firmware_version', '1.90'))
        self.loop_interval = float(options.get('loop_interval', 2.0))
        self.protocol_timeout = float(options.get('protocol_timeout', 10.0))
        self.max_dmpaft_records = int(options.get('max_dmpaft_records', 50000))
        if not 1 <= self.max_dmpaft_records <= MAX_DMPAFT_RECORDS:
            limited = max(1, min(MAX_DMPAFT_RECORDS, self.max_dmpaft_records))
            logerr(f"max_dmpaft_records = {self.max_dmpaft_records} is out of range, "
                   f"using {limited} (one download holds at most 65535 pages)")
            self.max_dmpaft_records = limited
        # What a client with no data of its own gets (DMPAFT without a time stamp):
        # 'recent' = the newest 2560 records, like a real console; 'all' = the whole
        # database from the oldest record, max_dmpaft_records per download.
        self.new_station_history = str(options.get('new_station_history', 'recent')).strip().lower()
        if self.new_station_history not in ('recent', 'all'):
            logerr(f"new_station_history = {self.new_station_history} is not 'recent' or 'all', "
                   f"using 'recent'")
            self.new_station_history = 'recent'

        self.running = True
        self.lock = threading.Lock()
        self.engine = engine
        self.config_dict = config_dict
        self.db_binding = options.get('binding', 'wx_binding')
        self.started = time.time()

        self.loop_state = {}
        self.server_sockets = []
        self.client_sockets = set()
        self.active_connections = 0

        self.port_mappings = {}
        raw_mapping = options.get('client_mapping', '')
        mapping_list = []
        if isinstance(raw_mapping, list):
            mapping_list = raw_mapping
        elif isinstance(raw_mapping, str) and raw_mapping.strip():
            mapping_list = raw_mapping.split(',')
        try:
            for pair in mapping_list:
                pair = pair.strip()
                if ':' in pair:
                    ip, p = pair.split(':')
                    self.port_mappings[int(p)] = ip.strip()
                    loginf(f"Configured VIP mapping: IP {ip.strip()} -> Port {p}")
        except Exception as e:
            logerr(f"Error parsing client_mapping: {e}")

        if self.port not in self.port_mappings:
            self.port_mappings[self.port] = None
            loginf(f"Default Port {self.port} is OPEN to all connections.")

        self.interval_fixed = 'archive_interval' in options
        self.davis_interval_mins = self._initial_archive_interval(engine, config_dict, options)
        self.eeprom = self._build_eeprom(engine, config_dict, options)
        self.stats = StationStats(config_dict, self.db_binding,
                                  rain_season_start=self.eeprom[EE_RAIN_SEASON_START] or 1)

        loginf(f"*** WLIP EMULATOR V{VERSION} ***")
        loginf(f"Station type {self.station_type}, archive interval {self.davis_interval_mins} min, "
               f"rain collector {self.rain_encoder().name}")
        loginf(f"Watchdog: Threshold={self.max_lag_threshold}s | Action={self.max_lag_action} | "
               f"Startup Delay={self.startup_delay}s")

        self.run_server()

        self.bind(weewx.NEW_LOOP_PACKET, self.handle_new_loop)
        self.bind(weewx.NEW_ARCHIVE_RECORD, self.handle_new_archive)
        if hasattr(weewx, 'SHUTDOWN'):
            self.bind(weewx.SHUTDOWN, self.shutDownEvent)

    # ---- set-up ----------------------------------------------------------

    @staticmethod
    def _initial_archive_interval(engine, config_dict, options):
        if 'archive_interval' in options:
            minutes = int(options['archive_interval'])
        else:
            seconds = None
            console = getattr(engine, 'console', None)
            try:
                seconds = int(console.archive_interval) if console is not None else None
            except Exception:
                seconds = None
            if not seconds:
                seconds = int(config_dict.get('StdArchive', {}).get('archive_interval', 300))
            minutes = seconds // 60
        if minutes > 255:
            loginf(f"WARNING: Archive interval {minutes} mins is too large for Davis byte. Clamping to 255.")
        return max(1, min(255, minutes))

    def _build_eeprom(self, engine, config_dict, options):
        eeprom = bytearray(b'\xff' * EEPROM_SIZE)
        eeprom[0:256] = EEPROM_TEMPLATE
        source = str(options.get('eeprom_source', 'auto')).lower()
        from_console = False
        console = getattr(engine, 'console', None)
        if source in ('auto', 'console') and hasattr(console, '_getEEPROM_value'):
            try:
                eeprom[0:256] = bytes(console._getEEPROM_value(0, '<256B'))
                from_console = True
                loginf("EEPROM image read from the Vantage console")
            except Exception as e:
                logerr(f"Could not read the console EEPROM, using a built-in image: {e}")
        elif source == 'console':
            logerr("eeprom_source = console, but WeeWX does not drive a Vantage. Using a built-in image.")

        if not from_console:
            self._apply_station_settings(eeprom, config_dict, options)

        bucket = str(options.get('rain_bucket', 'auto')).lower()
        if bucket != 'auto':
            bucket = int(bucket)
            if bucket not in (0, 1, 2):
                raise ValueError("rain_bucket must be 0 (0.01 in), 1 (0.2 mm) or 2 (0.1 mm)")
            eeprom[EE_SETUP_BITS] = (eeprom[EE_SETUP_BITS] & 0xCF) | (bucket << 4)
        if (eeprom[EE_SETUP_BITS] >> 4) & 0x03 == 3:
            eeprom[EE_SETUP_BITS] &= 0xCF  # invalid bucket code: use 0.01 in
        eeprom[EE_ARCHIVE_PERIOD] = self.davis_interval_mins
        return eeprom

    @staticmethod
    def _apply_station_settings(eeprom, config_dict, options):
        """Fill location, time zone, units and rain collector from weewx.conf."""
        station = config_dict.get('Station', {})
        lat = _num(options.get('latitude', station.get('latitude'))) or 0.0
        lon = _num(options.get('longitude', station.get('longitude'))) or 0.0
        struct.pack_into('<h', eeprom, EE_LATITUDE, int(round(lat * 10)))
        struct.pack_into('<h', eeprom, EE_LONGITUDE, int(round(lon * 10)))
        altitude_ft = 0.0
        alt = station.get('altitude')
        if isinstance(alt, (list, tuple)) and len(alt) == 2:
            value = _num(alt[0]) or 0.0
            altitude_ft = value / 0.3048 if str(alt[1]).startswith('meter') else value
        struct.pack_into('<h', eeprom, EE_ELEVATION, int(round(altitude_ft)))

        # Time zone as a GMT offset (standard time) with the current DST state.
        eeprom[EE_GMT_OR_ZONE] = 1
        eeprom[EE_MANUAL_OR_AUTO] = 1
        eeprom[EE_DAYLIGHT_SAVINGS] = 1 if time.localtime().tm_isdst > 0 else 0
        struct.pack_into('<h', eeprom, EE_GMT_OFFSET, int(round(-time.timezone / 36.0)))

        target = str(config_dict.get('StdConvert', {}).get('target_unit', 'US')).upper()
        metric = target != 'US'
        if metric:
            unit_bits = 0x7A    # hPa, degrees C, metres, mm, m/s
            bucket = 1          # 0.2 mm
        else:
            unit_bits = 0x00    # inHg, degrees F, feet, inches, mph
            bucket = 0          # 0.01 in
        eeprom[EE_UNIT_BITS] = unit_bits
        eeprom[EE_UNIT_BITS_COMP] = (~unit_bits) & 0xFF
        setup = 0x01 | 0x08 | (bucket << 4)   # 24 h clock, large wind cups
        if metric:
            setup |= 0x04                     # day/month date format
        if lat >= 0:
            setup |= 0x40                     # north
        if lon >= 0:
            setup |= 0x80                     # east
        eeprom[EE_SETUP_BITS] = setup
        eeprom[EE_RAIN_SEASON_START] = int(options.get('rain_season_start', 1))

    def rain_encoder(self):
        return RainEncoder((self.eeprom[EE_SETUP_BITS] >> 4) & 0x03)

    # ---- EEPROM access ---------------------------------------------------

    def eeprom_read(self, address, length):
        self.sync_archive_interval()
        with self.lock:
            data = bytes(self.eeprom[address:address + length])
        return data + b'\xff' * (length - len(data))

    def eeprom_write(self, address, data, peer):
        loginf(f"{peer} wrote EEPROM {address:#05x}: {data.hex(' ')} (kept in memory only)")
        with self.lock:
            for i, v in enumerate(data):
                if 0 <= address + i < EEPROM_SIZE:
                    self.eeprom[address + i] = v

    # ---- events ----------------------------------------------------------

    def shutDownEvent(self, event):
        self.shutDown()

    def shutDown(self):
        """Stops the server gracefully. Safe to call more than once."""
        if not self.running:
            return
        loginf("Shutting down WLIP Server gracefully...")
        self.running = False
        with self.lock:
            sockets = list(self.server_sockets) + list(self.client_sockets)
            self.server_sockets = []
        for sock in sockets:
            try:
                sock.close()
            except OSError as e:
                logerr(f"Error during shutdown: {e}")

    def handle_new_loop(self, event):
        packet = event.packet
        try:
            packet_us = weewx.units.to_std_system(packet, weewx.US)
        except Exception as e:
            logerr(f"Could not convert LOOP packet to US units: {e}")
            return
        with self.lock:
            # Merge: LOOP1 and LOOP2 packets from a Vantage carry different fields.
            self.loop_state.update({k: v for k, v in packet_us.items() if v is not None})
            self.loop_state['dateTime'] = packet_us.get('dateTime', time.time())
        if self.debug_detail >= 1:
            logdbg(f"INTERNAL UPDATE: WeeWX pushed new packet. TS={packet.get('dateTime')} "
                   f"Temp={packet.get('outTemp')}")

    def handle_new_archive(self, event):
        self.stats.mark_dirty()

    # ---- data for the protocol ------------------------------------------

    def sync_archive_interval(self):
        """Keep EEPROM 0x2D equal to the interval of the records in the database."""
        if self.interval_fixed:
            return
        self.stats.get()
        minutes = self.stats.archive_interval_min
        if minutes:
            minutes = max(1, min(255, minutes))
            if minutes != self.davis_interval_mins:
                loginf(f"Archive interval from the database: {minutes} min")
                self.davis_interval_mins = minutes
                with self.lock:
                    self.eeprom[EE_ARCHIVE_PERIOD] = minutes

    def current_values(self):
        """Latest LOOP values (US units) with database-derived fallbacks."""
        with self.lock:
            p = dict(self.loop_state)
        stats, _ = self.stats.get()
        for key in ('dayRain', 'monthRain', 'yearRain', 'dayET', 'monthET', 'yearET',
                    'rain15', 'hourRain', 'rain24'):
            if _num(p.get(key)) is None and stats.get(key) is not None:
                p[key] = stats[key]
        if _num(p.get('sunrise')) is None or _num(p.get('sunset')) is None:
            p.update(self._sun_times())
        trend = p.get('trendIcon')
        if _num(trend) is None or int(trend) not in (-60, -20, 0, 20, 60):
            trend = self._bar_trend(p.get('barometer'), stats.get('barometer_3h_ago'))
        p['_bar_trend'] = int(trend)
        return p

    def _sun_times(self):
        if weeutil_sun is None:
            return {}
        with self.lock:
            lat = struct.unpack_from('<h', self.eeprom, EE_LATITUDE)[0] / 10.0
            lon = struct.unpack_from('<h', self.eeprom, EE_LONGITUDE)[0] / 10.0
        today = datetime.date.today()
        try:
            rise_utc, set_utc = weeutil_sun.sunRiseSet(today.year, today.month, today.day, lon, lat)
        except Exception:
            return {}
        midnight_utc = calendar.timegm(today.timetuple())
        return {'sunrise': midnight_utc + rise_utc * 3600.0,
                'sunset': midnight_utc + set_utc * 3600.0}

    @staticmethod
    def _bar_trend(now_bar, old_bar):
        """Davis 3-hour barometer trend: -60, -20, 0, 20 or 60."""
        a, b = _num(now_bar), _num(old_bar)
        if a is None or b is None:
            return 0
        change = a - b
        if change >= 0.06:
            return 60
        if change >= 0.02:
            return 20
        if change <= -0.06:
            return -60
        if change <= -0.02:
            return -20
        return 0

    def next_record_index(self):
        """Where the console would write its next archive record."""
        last = self.stats.last_archive_ts or time.time()
        return int(last // (self.davis_interval_mins * 60)) + 1

    def loop_packet(self, kind):
        p = self.current_values()
        trend = p.pop('_bar_trend', 0)
        rain = self.rain_encoder()
        if kind == 2:
            return build_loop2(p, rain, trend)
        return build_loop1(p, rain, self.next_record_index(), trend)

    def hilows(self):
        _, hilows = self.stats.get()
        return hilows

    def rxcheck_text(self):
        received = int((time.time() - self.started) / 2.5)
        return f" {received} 0 0 {received} 0"

    def bardata_text(self):
        p = self.current_values()
        bar = enc(p.get('barometer'), 1000, 0, 0, 65535, 0)
        with self.lock:
            elevation = struct.unpack_from('<h', self.eeprom, EE_ELEVATION)[0]
        dew = enc(p.get('dewpoint'), 1, 0, -200, 200, 0)
        temp = enc(p.get('outTemp'), 1, 0, -200, 200, 0)
        return (f"BAR {bar}\n\rELEVATION {elevation}\n\rDEW POINT {dew}\n\r"
                f"VIRTUAL TEMP {temp}\n\rC 0\n\rR 1000\n\rBARCAL 0\n\r"
                f"GAIN 0\n\rOFFSET 0\n\r")

    # Records per database query during an archive download. Every query is short, so
    # the emulator never holds a read lock long enough to make WeeWX's own archive write
    # fail with "database is locked" (weewxd restarts its engine on that error).
    DOWNLOAD_CHUNK = 100

    def _with_db(self, work, default):
        """Run work(dbm) on a fresh database manager, retrying while the database is locked."""
        for attempt in range(3):
            try:
                with weewx.manager.open_manager_with_config(self.config_dict, self.db_binding) as dbm:
                    return work(dbm)
            except DB_LOCKED_ERRORS as e:
                logdbg(f"DB locked, retrying ({attempt + 1}/3): {e}")
                time.sleep(0.5)
            except Exception as e:
                logerr(f"DB ERROR: Failed to query database: {e}")
                return default
        logerr("DB ERROR: Database stayed locked")
        return default

    @staticmethod
    def _fetch_after(dbm, after_ts, limit):
        """At most 'limit' raw archive records after after_ts, oldest first. Both queries
        run to completion, so no read lock is held afterwards."""
        for attempt in range(3):
            try:
                row = dbm.getSql(f"SELECT dateTime FROM {dbm.table_name} WHERE dateTime > ? "
                                 f"ORDER BY dateTime ASC LIMIT 1 OFFSET {int(limit) - 1}", (after_ts,))
                return list(dbm.genBatchRecords(after_ts, row[0] if row else None))[:limit]
            except DB_LOCKED_ERRORS as e:
                logdbg(f"DB locked, retrying ({attempt + 1}/3): {e}")
                time.sleep(0.5)
        logerr("DB ERROR: Database stayed locked")
        return []

    def plan_download(self, since_ts, whole_history=False):
        """(after_ts, count) for DMPAFT, from two quick index queries so the page header
        can go out at once. Without a time stamp the client gets what a real console holds
        (the newest 2560 records), or with whole_history everything from the oldest record;
        with one it may get more (extended catch-up)."""
        def work(dbm):
            table = dbm.table_name
            after = since_ts
            if after is None and whole_history:
                after = 0
            elif after is None:
                row = dbm.getSql(f"SELECT dateTime FROM {table} ORDER BY dateTime DESC "
                                 f"LIMIT 1 OFFSET {HARDWARE_RECORD_LIMIT - 1}")
                after = row[0] - 1 if row else 0
            if since_ts is None and not whole_history:
                limit = HARDWARE_RECORD_LIMIT
            else:
                limit = self.max_dmpaft_records
            # Count no further than the limit: a plain COUNT(*) over years of records
            # took more than 3 seconds on a cold cache, longer than the client waits.
            row = dbm.getSql(f"SELECT COUNT(*) FROM (SELECT dateTime FROM {table} "
                             f"WHERE dateTime > ? LIMIT {int(limit)}) AS wanted", (after,))
            return after, (row[0] if row else 0)
        return self._with_db(work, (0, 0))

    def _iter_records(self, after_ts, count):
        """(timestamp, packed record) pairs after after_ts, oldest first, at most 'count'.
        Read DOWNLOAD_CHUNK records at a time, so no cursor stays open while the client
        works through the pages."""
        if count <= 0:
            return
        self.sync_archive_interval()
        rain = self.rain_encoder()
        done = 0
        try:
            with weewx.manager.open_manager_with_config(self.config_dict, self.db_binding) as dbm:
                while done < count:
                    chunk = self._fetch_after(dbm, after_ts, min(self.DOWNLOAD_CHUNK, count - done))
                    if not chunk:
                        return
                    for record in chunk:
                        after_ts = record['dateTime']
                        done += 1
                        try:
                            rec_us = weewx.units.to_std_system(record, weewx.US)
                            packed = build_archive_record(rec_us, rain, self.davis_interval_mins)
                        except Exception as e:
                            logerr(f"Skipping archive record {after_ts}: {e}")
                            continue
                        yield int(after_ts), packed
        except Exception as e:
            logerr(f"DB ERROR: Failed to read archive records: {e}")

    def iter_archive_records(self, after_ts, count):
        """Packed records for DMPAFT, produced as the client asks for pages."""
        return (packed for _, packed in self._iter_records(after_ts, count))

    def archive_memory_image(self):
        """2560 record slots laid out like the console's circular archive memory."""
        after, count = self.plan_download(None)
        interval_s = self.davis_interval_mins * 60
        slots = [None] * HARDWARE_RECORD_LIMIT
        for ts, packed in self._iter_records(after, count):
            slots[(ts // interval_s) % HARDWARE_RECORD_LIMIT] = packed
        return slots

    # ---- watchdog --------------------------------------------------------

    def watchdog_ok(self, session):
        """Checks how old the newest WeeWX data is before streaming LOOP packets."""
        with self.lock:
            ts = self.loop_state.get('dateTime')
            temp = self.loop_state.get('outTemp')
            wind = self.loop_state.get('windSpeed')
        if ts is None:
            if self.debug_detail >= 1:
                logdbg("Sending LOOP packets... [No Data]")
            return True
        lag = int(time.time() - ts)
        if self.debug_detail >= 1:
            t_str = f"{temp:.1f}F" if isinstance(temp, (int, float)) else "None"
            w_str = f"{wind:.1f}mph" if isinstance(wind, (int, float)) else "None"
            logdbg(f"Sending LOOP packets... [Lag: {lag}s | Temp: {t_str} | Wind: {w_str}]")
        if self.max_lag_threshold <= 0 or lag <= self.max_lag_threshold:
            return True
        msg = f"WATCHDOG TRIGGERED: Data lag is {lag} seconds (Threshold: {self.max_lag_threshold})."
        if self.max_lag_action == 2:
            logcrit(f"{msg} SENDING SIGTERM TO WEEWX FOR SYSTEMD RESTART.")
            try:
                session.sock.close()
            except OSError:
                pass
            os.kill(os.getpid(), signal.SIGTERM)
            return False
        if self.max_lag_action == 1:
            logerr(f"{msg} Disconnecting client.")
            try:
                session.sock.close()
            except OSError:
                pass
            return False
        logerr(msg)
        return True

    # ---- network ---------------------------------------------------------

    def run_server(self):
        t = threading.Thread(target=self._server_coordinator, name='wlip-coordinator')
        t.daemon = True
        t.start()

    def _server_coordinator(self):
        if self.startup_delay > 0:
            loginf(f"Startup delay active: Waiting {self.startup_delay} seconds...")
            time.sleep(self.startup_delay)
            loginf("Startup delay complete. Opening ports.")
        for port, allowed_ip in self.port_mappings.items():
            t = threading.Thread(target=self.listen_on_port, args=(port, allowed_ip),
                                 name=f'wlip-listen-{port}')
            t.daemon = True
            t.start()
            time.sleep(0.1)

    def listen_on_port(self, port, allowed_ip):
        access_msg = "ALL (Default)" if allowed_ip is None else allowed_ip
        loginf(f"Starting listener on port {port} [Allowed: {access_msg}]")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Timeout lets the thread check self.running during shutdown
        sock.settimeout(1.0)
        try:
            sock.bind(('0.0.0.0', port))
            sock.listen(self.max_clients)
            with self.lock:
                self.server_sockets.append(sock)
            while self.running:
                try:
                    client_sock, addr = sock.accept()
                except socket.timeout:
                    continue
                except OSError as e:
                    if self.running:
                        logerr(f"Socket accept error on port {port}: {e}")
                    break
                if allowed_ip is not None and addr[0] != allowed_ip:
                    client_sock.close()
                    continue
                with self.lock:
                    too_many = self.active_connections >= self.max_clients
                if too_many:
                    loginf(f"Refusing {addr[0]}:{addr[1]}: {self.max_clients} clients already connected")
                    client_sock.close()
                    continue
                client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                client_sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                if hasattr(socket, 'TCP_KEEPIDLE'):
                    client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
                    client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
                    client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 3)
                client_sock.settimeout(None)
                t = threading.Thread(target=self.handle_client, args=(client_sock, addr),
                                     name=f'wlip-client-{addr[0]}:{addr[1]}')
                t.daemon = True
                t.start()
        except Exception as e:
            logerr(f"Fatal error on port {port}: {e}")
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def handle_client(self, client_sock, addr):
        with self.lock:
            self.active_connections += 1
            self.client_sockets.add(client_sock)
            count = self.active_connections
        try:
            local_port = client_sock.getsockname()[1]
        except OSError:
            local_port = "Unknown"
        loginf(f"Incoming connection from {addr[0]}:{addr[1]} to PORT {local_port}. Active: {count}")
        try:
            ClientSession(self, client_sock, addr).run()
        except (ConnectionError, OSError) as e:
            if self.running:
                logdbg(f"Client connection closed/error: {e}")
        except Exception as e:
            logerr(f"Unexpected error with client {addr[0]}:{addr[1]}: {e!r}")
        finally:
            try:
                client_sock.close()
            except OSError:
                pass
            with self.lock:
                self.active_connections -= 1
                self.client_sockets.discard(client_sock)
                count = self.active_connections
            loginf(f"Connection closed from {addr[0]}:{addr[1]}. Active: {count}")
