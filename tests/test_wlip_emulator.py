"""End-to-end tests for the WLIP emulator.

The emulator runs against a temporary WeeWX database and is exercised over TCP by
  * a byte-for-byte replay of what WeatherLink PC sent in a Wireshark capture
    (including how it splits commands over TCP segments), and
  * the WeeWX Vantage driver itself, in Ethernet (WeatherLinkIP) mode.
Packets are decoded with the WeeWX Vantage driver's own decoding routines.

Run with WeeWX on the Python path, for example:
    PYTHONPATH=/path/to/weewx/src python3 -m unittest discover -s tests -v
"""

import logging
import math
import os
import shutil
import socket
import sqlite3
import struct
import sys
import tempfile
import time
import unittest

import configobj

import weewx
import weewx.drivers.vantage as vantage
import weewx.manager
import weewx.units
from weewx.crc16 import crc16

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import wlip_emulator  # noqa: E402

logging.basicConfig(level=os.environ.get('WLIP_TEST_LOG', 'WARNING'),
                    format='%(asctime)s %(threadName)s %(levelname)s %(message)s')

INTERVAL = 300
N_RECORDS = 3 * 288          # three days of 5-minute records
ACK = b'\x06'


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def synthetic_record(i, ts):
    """A METRICWX archive record with values that exercise the encoders."""
    return {
        'dateTime': ts, 'usUnits': weewx.METRICWX, 'interval': INTERVAL // 60,
        'outTemp': round(5.0 + 8.0 * math.sin(i / 40.0), 1),
        'inTemp': 21.5, 'outHumidity': 70.0 + (i % 20), 'inHumidity': 38.0,
        'barometer': round(1005.0 + 10.0 * math.sin(i / 100.0), 1),
        'pressure': 998.0, 'altimeter': 1006.0,
        'windSpeed': round(2.0 + 1.5 * math.sin(i / 7.0), 1),
        'windGust': round(4.0 + 2.0 * math.sin(i / 7.0), 1),
        'windDir': float((i * 37) % 360), 'windGustDir': float((i * 41) % 360),
        'rain': 0.2 if i % 7 == 0 else 0.0, 'rainRate': 1.2 if i % 7 == 0 else 0.0,
        'radiation': float(max(0, int(400 * math.sin(i / 50.0)))),
        'UV': round(max(0.0, 3.0 * math.sin(i / 50.0)), 1),
        'ET': 0.0, 'dewpoint': 1.5, 'windchill': 4.0, 'heatindex': 5.0,
        'rxCheckPercent': 95.0,
    }


class EmulatorTestBase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix='wlip-test-')
        cls.port = free_port()
        cls.config = configobj.ConfigObj({
            'WEEWX_ROOT': cls.tmpdir,
            'Station': {'latitude': '61.1', 'longitude': '22.4', 'altitude': ['60', 'meter'],
                        'station_type': 'Simulator'},
            'StdConvert': {'target_unit': 'METRICWX'},
            'StdArchive': {'archive_interval': str(INTERVAL)},
            'DataBindings': {'wx_binding': {
                'database': 'archive_sqlite', 'table_name': 'archive',
                'manager': 'weewx.manager.DaySummaryManager',
                'schema': 'weewx.schemas.wview_extended.schema'}},
            'Databases': {'archive_sqlite': {'database_name': 'weewx.sdb', 'database_type': 'SQLite'}},
            'DatabaseTypes': {'SQLite': {'driver': 'weedb.sqlite', 'SQLITE_ROOT': cls.tmpdir}},
            'WeatherLinkEmulator': {
                'port': str(cls.port), 'debug_detail': os.environ.get('WLIP_DEBUG', '0'),
                'startup_delay': '0', 'loop_interval': '0.3', 'protocol_timeout': '3',
            },
        })
        now = int(time.time())
        cls.last_ts = now - now % INTERVAL
        cls.first_ts = cls.last_ts - (N_RECORDS - 1) * INTERVAL
        cls.records = [synthetic_record(i, cls.first_ts + i * INTERVAL) for i in range(N_RECORDS)]
        with weewx.manager.open_manager_with_config(cls.config, 'wx_binding', initialize=True) as dbm:
            dbm.addRecord(cls.records, log_success=False)

        class FakeEngine(object):
            console = None

            def bind(self, event_type, callback):
                pass

        cls.emulator = wlip_emulator.WeatherLinkEmulator(FakeEngine(), cls.config)
        cls.loop_in = {
            'dateTime': now, 'usUnits': weewx.METRICWX, 'outTemp': -3.4, 'inTemp': 22.1,
            'outHumidity': 87.0, 'inHumidity': 41.0, 'barometer': 1012.3, 'windSpeed': 4.5,
            'windDir': 0.0, 'windGust': 7.2, 'windGustDir': 350.0, 'rainRate': 2.4,
            'dayRain': 3.6, 'radiation': 120.0, 'UV': 1.4, 'dewpoint': -5.0,
            'windchill': -8.0, 'heatindex': -3.4, 'extraTemp1': 10.0,
        }
        cls.emulator.handle_new_loop(weewx.Event(weewx.NEW_LOOP_PACKET, packet=dict(cls.loop_in)))
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                socket.create_connection(('127.0.0.1', cls.port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.emulator.shutDown()
        time.sleep(0.2)
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    # ---- helpers ---------------------------------------------------------

    @staticmethod
    def decoder():
        """A Vantage driver instance used only for its decoding routines."""
        v = vantage.Vantage.__new__(vantage.Vantage)
        v.rain_bucket_type = 1
        v.hardware_type = 16
        v.model_type = 2
        v.iss_id = 1
        v.archive_interval_ = INTERVAL
        v.save_day_rain = None
        return v

    def connect(self):
        s = socket.create_connection(('127.0.0.1', self.port), timeout=3)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.addCleanup(s.close)
        return s

    @staticmethod
    def recv_exact(s, n, timeout=3.0):
        s.settimeout(timeout)
        data = b''
        while len(data) < n:
            chunk = s.recv(n - len(data))
            if not chunk:
                raise ConnectionError('closed')
            data += chunk
        return data

    def assert_silent(self, s, seconds=0.3):
        s.settimeout(seconds)
        try:
            extra = s.recv(100)
        except socket.timeout:
            return
        self.fail(f'unexpected bytes: {extra!r}')

    def wake(self, s):
        s.sendall(b'\n')
        self.assertEqual(self.recv_exact(s, 2), b'\n\r')

    def us(self, rec):
        return weewx.units.to_std_system(rec, weewx.US)


class TestWeatherLinkPCReplay(EmulatorTestBase):
    """Replays the WeatherLink PC session from the Wireshark capture."""

    def test_station_type_is_vantage_pro2_and_leaves_no_garbage(self):
        s = self.connect()
        self.wake(s)
        self.wake(s)
        # WeatherLink PC sends "WRD" and "\x12\x4d\r" as two TCP segments.
        s.sendall(b'WRD')
        time.sleep(0.05)
        s.sendall(b'\x12\x4d\r')
        self.assertEqual(self.recv_exact(s, 2), b'\x06\x10')
        self.assert_silent(s)
        # The next wake-up must get LF CR, not the NAK the old version sent.
        self.wake(s)
        # Other spellings: WeeWX sends it in one piece with LF, some clients omit the
        # argument bytes.
        for command in (b'WRD\x12\x4d\n', b'WRD\x12\x4d\r\n', b'WRD\n'):
            s.sendall(command)
            self.assertEqual(self.recv_exact(s, 2), b'\x06\x10', command)
            self.assert_silent(s, 0.2)
            self.wake(s)

    def test_eebrd_single_bytes(self):
        s = self.connect()
        eeprom = self.emulator.eeprom
        for address in (0x29, 0x2B, 0x2C, 0x2D, 0x17, 0x11, 0x12, 0x13, 0x16):
            self.wake(s)
            command = b'EEBRD %X 1\n' % address           # 11 bytes, as in the capture
            self.assertEqual(len(command), 11)
            s.sendall(command)
            reply = self.recv_exact(s, 4)
            self.assertEqual(reply[0:1], ACK)
            self.assertEqual(crc16(reply[1:]), 0)
            self.assertEqual(reply[1], eeprom[address], hex(address))
        # Setup bits: 24 h, day/month, large cups, 0.2 mm collector, north, east.
        self.assertEqual(eeprom[0x2B], 0xDD)
        self.assertEqual(eeprom[0x2D], INTERVAL // 60)

    def test_ver(self):
        s = self.connect()
        self.wake(s)
        s.sendall(b'VER\n')
        self.assertEqual(self.recv_exact(s, 19), b'\n\rOK\n\rMay  1 2012\n\r')

    def dmpaft(self, s, since_ts, split=True):
        """DMPAFT the way WeatherLink PC does it: time stamp sent as 1 + 5 bytes."""
        self.wake(s)
        s.sendall(b'DMPAFT\n')
        self.assertEqual(self.recv_exact(s, 1), ACK)
        if since_ts:
            tt = time.localtime(since_ts)
            stamp = struct.pack('<HH', tt.tm_mday + (tt.tm_mon << 5) + ((tt.tm_year - 2000) << 9),
                                tt.tm_hour * 100 + tt.tm_min)
        else:
            stamp = b'\x00\x00\x00\x00'
        stamp += struct.pack('>H', crc16(stamp))
        if split:
            s.sendall(stamp[:1])
            time.sleep(0.045)
            s.sendall(stamp[1:])
        else:
            s.sendall(stamp)
        self.assertEqual(self.recv_exact(s, 1), ACK)
        header = self.recv_exact(s, 6)
        self.assertEqual(crc16(header), 0)
        return struct.unpack('<HH', header[:4])

    def download(self, s, npages, start_index):
        decoder = self.decoder()
        out = []
        for _ in range(npages):
            s.sendall(ACK)
            page = self.recv_exact(s, 267)
            self.assertEqual(crc16(page), 0)
            for idx in range(start_index, 5):
                raw = page[1 + 52 * idx:53 + 52 * idx]
                if raw[:4] == b'\xff' * 4:
                    continue
                out.append(decoder._unpackArchivePacket(raw))
            start_index = 0
        s.sendall(ACK)    # WeatherLink PC acknowledges the last page as well
        return out

    def test_dmpaft_with_split_timestamp(self):
        s = self.connect()
        since = self.last_ts - 20 * INTERVAL
        npages, start = self.dmpaft(s, since)
        self.assertEqual(npages, 4)          # 20 records
        got = self.download(s, npages, start)
        self.assertEqual([r['dateTime'] for r in got],
                         [r['dateTime'] for r in self.records[-20:]])
        for rec, src in zip(got, self.records[-20:]):
            ref = self.us(src)
            self.assertAlmostEqual(rec['outTemp'], ref['outTemp'], delta=0.06)
            self.assertAlmostEqual(rec['barometer'], ref['barometer'], delta=0.001)
            self.assertAlmostEqual(rec['windSpeed'], ref['windSpeed'], delta=0.51)
            self.assertAlmostEqual(rec['rain'], ref['rain'], delta=0.0001)
            self.assertAlmostEqual(rec['outHumidity'], ref['outHumidity'], delta=0.5)
            self.assertEqual(rec['windDir'], round(src['windDir'] / 22.5) % 16 * 22.5)
            self.assertAlmostEqual(rec['rxCheckPercent'], 95.0, delta=1.0)
        # The session is still in step after the download.
        self.wake(s)
        s.sendall(b'TEST\n')
        self.assertEqual(self.recv_exact(s, 8), b'\n\rTEST\n\r')

    def test_dmpaft_zero_timestamp_gets_everything(self):
        s = self.connect()
        npages, start = self.dmpaft(s, None, split=False)
        self.assertEqual(npages, math.ceil(N_RECORDS / 5))
        got = self.download(s, npages, start)
        self.assertEqual(len(got), N_RECORDS)

    def test_new_station_history(self):
        """A station with no data (time stamp 0) gets the newest records like a real
        console, or with new_station_history = all the whole database from the oldest
        record, max_dmpaft_records at a time."""
        self.emulator.max_dmpaft_records = 100
        self.addCleanup(setattr, self.emulator, 'max_dmpaft_records', 50000)
        s = self.connect()
        npages, start = self.dmpaft(s, None, split=False)
        got = self.download(s, npages, start)
        self.assertEqual([r['dateTime'] for r in got], [r['dateTime'] for r in self.records])

        self.emulator.new_station_history = 'all'
        self.addCleanup(setattr, self.emulator, 'new_station_history', 'recent')
        npages, start = self.dmpaft(s, None, split=False)
        self.assertEqual(npages, 20)
        got = self.download(s, npages, start)
        self.assertEqual([r['dateTime'] for r in got], [r['dateTime'] for r in self.records[:100]])
        npages, start = self.dmpaft(s, got[-1]['dateTime'])
        got = self.download(s, npages, start)
        self.assertEqual([r['dateTime'] for r in got], [r['dateTime'] for r in self.records[100:200]])

    def test_dmpaft_catch_up_is_limited_and_continues(self):
        """With an old time stamp each download holds at most max_dmpaft_records, oldest
        first, and the next download carries on from the newest record received."""
        self.emulator.max_dmpaft_records = 100
        self.addCleanup(setattr, self.emulator, 'max_dmpaft_records', 50000)
        s = self.connect()
        npages, start = self.dmpaft(s, self.first_ts - 10 * 86400)
        self.assertEqual(npages, 20)
        got = self.download(s, npages, start)
        self.assertEqual([r['dateTime'] for r in got], [r['dateTime'] for r in self.records[:100]])
        npages, start = self.dmpaft(s, got[-1]['dateTime'])
        self.assertEqual(npages, 20)
        got = self.download(s, npages, start)
        self.assertEqual([r['dateTime'] for r in got], [r['dateTime'] for r in self.records[100:200]])

    def test_dmpaft_up_to_date_returns_no_pages(self):
        s = self.connect()
        npages, _ = self.dmpaft(s, self.last_ts)
        self.assertEqual(npages, 0)
        self.wake(s)

    def test_dmpaft_resend_and_cancel(self):
        s = self.connect()
        npages, _ = self.dmpaft(s, self.last_ts - 30 * INTERVAL)
        self.assertEqual(npages, 6)
        s.sendall(ACK)
        first = self.recv_exact(s, 267)
        s.sendall(b'\x15')                   # ask for the same page again
        self.assertEqual(self.recv_exact(s, 267), first)
        s.sendall(b'\x21')
        self.assertEqual(self.recv_exact(s, 267), first)
        s.sendall(ACK)
        second = self.recv_exact(s, 267)
        self.assertEqual(second[0], 1)
        s.sendall(b'\x1b')                   # ESC: stop
        self.assert_silent(s)
        self.wake(s)

    def test_dmpaft_old_timestamp_from_capture(self):
        """The DMPAFT exchange from a WeatherLink PC capture: its newest record is from
        2020-10-04 15:04 and the time stamp arrives as 1 + 5 bytes. The first V91 read
        every record before sending the page header: WeatherLink PC gave up after about
        3.5 seconds, and the long read locked the database so WeeWX's archive write failed
        and weewxd restarted its engine. The header must come at once, however slow the
        reads, and nothing may hold a read lock while the client works through the pages."""
        real_fetch = self.emulator._fetch_after

        def slow_fetch(dbm, after_ts, limit):
            time.sleep(0.3)
            return real_fetch(dbm, after_ts, limit)
        self.emulator._fetch_after = slow_fetch
        self.addCleanup(delattr, self.emulator, '_fetch_after')

        s = self.connect()
        self.wake(s)
        s.sendall(b'DMPAFT\n')
        self.assertEqual(self.recv_exact(s, 1), ACK)
        s.sendall(bytes.fromhex('44'))
        time.sleep(0.045)
        s.sendall(bytes.fromhex('29 e0 05 fc 2d'))
        self.assertEqual(self.recv_exact(s, 1), ACK)
        started = time.monotonic()
        header = self.recv_exact(s, 6)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(crc16(header), 0)
        npages, start = struct.unpack('<HH', header[:4])
        self.assertEqual((npages, start), (math.ceil(N_RECORDS / 5), 0))

        decoder = self.decoder()
        got = []
        for n in range(npages):
            s.sendall(ACK)
            page = self.recv_exact(s, 267)
            self.assertEqual((crc16(page), page[0]), (0, n & 0xFF))
            got.extend(decoder._unpackArchivePacket(page[1 + 52 * i:53 + 52 * i])
                       for i in range(5) if page[1 + 52 * i:5 + 52 * i] != b'\xff' * 4)
            if n == npages // 2:
                db = sqlite3.connect(os.path.join(self.tmpdir, 'weewx.sdb'), timeout=0.2,
                                     isolation_level=None)
                try:
                    db.execute('BEGIN EXCLUSIVE')    # fails while anyone holds a read lock
                    db.execute('ROLLBACK')
                finally:
                    db.close()
        s.sendall(ACK)
        self.assertEqual([r['dateTime'] for r in got], [r['dateTime'] for r in self.records])
        self.wake(s)

    def test_dmpaft_bad_crc_is_cancelled(self):
        s = self.connect()
        self.wake(s)
        s.sendall(b'DMPAFT\n')
        self.assertEqual(self.recv_exact(s, 1), ACK)
        s.sendall(b'\x01\x02\x03\x04\x00\x00')
        self.assertEqual(self.recv_exact(s, 1), b'\x18')
        self.wake(s)

    def test_settime_and_eebwr(self):
        s = self.connect()
        self.wake(s)
        s.sendall(b'SETTIME\n')
        self.assertEqual(self.recv_exact(s, 1), ACK)
        payload = bytes([10, 20, 12, 4, 11, 126])
        s.sendall(payload + struct.pack('>H', crc16(payload)))
        self.assertEqual(self.recv_exact(s, 1), ACK)
        self.wake(s)
        s.sendall(b'EEBWR 2D 01\n')
        self.assertEqual(self.recv_exact(s, 1), ACK)
        s.sendall(b'\x05' + struct.pack('>H', crc16(b'\x05')))
        self.assertEqual(self.recv_exact(s, 1), ACK)

    def test_text_commands(self):
        s = self.connect()
        for command, expect in ((b'NVER\n', b'\n\rOK\n\r1.90\n\r'),
                                (b'RXTEST\n', b'\n\rOK\n\r'),
                                (b'LAMPS 1\n', b'\n\rOK\n\r'),
                                (b'START\n', b'\n\rOK\n\r')):
            self.wake(s)
            s.sendall(command)
            self.assertEqual(self.recv_exact(s, len(expect)), expect)
            self.assert_silent(s, 0.1)

    def test_hilows(self):
        s = self.connect()
        self.wake(s)
        s.sendall(b'HILOWS\n')
        self.assertEqual(self.recv_exact(s, 1), ACK)
        block = self.recv_exact(s, 438)
        self.assertEqual(crc16(block), 0)
        day_start = time.mktime(time.localtime(time.time())[:3] + (0, 0, 0, 0, 0, -1))
        today = [self.us(r) for r in self.records if r['dateTime'] > day_start]
        if today:
            hi = max(r['outTemp'] for r in today)
            lo = min(r['outTemp'] for r in today)
            day_lo, day_hi = struct.unpack_from('<hh', block, 47)
            self.assertAlmostEqual(day_hi / 10.0, hi, delta=0.06)
            self.assertAlmostEqual(day_lo / 10.0, lo, delta=0.06)

    def test_loop_packets(self):
        s = self.connect()
        self.wake(s)
        s.sendall(b'LPS 3 2\n')
        self.assertEqual(self.recv_exact(s, 1), ACK)
        decoder = self.decoder()
        p1 = self.recv_exact(s, 99)
        p2 = self.recv_exact(s, 99)
        for p in (p1, p2):
            self.assertEqual(crc16(p), 0)
            self.assertEqual(p[95:97], b'\n\r')
        self.assertEqual((p1[4], p2[4]), (0, 1))
        loop1 = decoder._unpackLoopPacket(p1[:95])
        loop2 = decoder._unpackLoopPacket(p2[:95])
        ref = self.us(self.loop_in)
        for loop in (loop1, loop2):
            self.assertAlmostEqual(loop['outTemp'], ref['outTemp'], delta=0.06)
            self.assertAlmostEqual(loop['barometer'], ref['barometer'], delta=0.001)
            self.assertAlmostEqual(loop['dayRain'], ref['dayRain'], delta=0.0001)
            self.assertAlmostEqual(loop['rainRate'], ref['rainRate'], delta=0.0001)
            self.assertEqual(loop['windDir'], 0.0)          # north, sent as 360
        self.assertAlmostEqual(loop1['extraTemp1'], ref['extraTemp1'], delta=0.51)
        self.assertNotIn('extraTemp2', loop1)                # dashed
        self.assertAlmostEqual(loop2['dewpoint'], ref['dewpoint'], delta=0.51)
        self.assertAlmostEqual(loop2['windGust10'], ref['windGust'], delta=0.51)
        self.assertIn('sunrise', loop1)

    def test_loop_stream_stops_on_wakeup(self):
        s = self.connect()
        self.wake(s)
        s.sendall(b'LOOP 100\n')
        self.assertEqual(self.recv_exact(s, 1), ACK)
        self.recv_exact(s, 99)
        s.sendall(b'\n')
        # At most one more packet may already be on its way, then LF CR.
        data = self.recv_exact(s, 2, timeout=2)
        if data != b'\n\r':
            data += self.recv_exact(s, 99)
            self.assertEqual(data[-2:], b'\n\r')
        self.assert_silent(s, 0.8)


class TestWeeWXVantageDriver(EmulatorTestBase):
    """The WeeWX Vantage driver in WeatherLinkIP mode talking to the emulator."""

    def station(self):
        st = vantage.Vantage(type='ethernet', host='127.0.0.1', tcp_port=self.port,
                             tcp_send_delay=0.05, command_delay=0.2, wait_before_retry=0.2,
                             timeout=3, max_tries=2, loop_request=3, iss_id=1)
        self.addCleanup(st.closePort)
        return st

    def test_setup(self):
        st = self.station()
        self.assertEqual(st.hardware_type, 16)
        self.assertEqual(st.hardware_name, 'Vantage Pro2')
        self.assertEqual(st.rain_bucket_type, 1)
        self.assertEqual(st.archive_interval, INTERVAL)
        self.assertEqual(st.barometer_unit, 'hPa')
        self.assertEqual(st.getFirmwareDate(), b'May  1 2012')

    def test_catch_up(self):
        st = self.station()
        since = self.last_ts - 12 * INTERVAL
        got = list(st.genArchiveRecords(since))
        self.assertEqual([r['dateTime'] for r in got], [r['dateTime'] for r in self.records[-12:]])

    def test_loop(self):
        st = self.station()
        packets = list(st.genDavisLoopPackets(4))
        self.assertEqual(len(packets), 4)
        ref = self.us(self.loop_in)
        for p in packets:
            self.assertAlmostEqual(p['outTemp'], ref['outTemp'], delta=0.06)

    def test_time_and_diagnostics(self):
        st = self.station()
        self.assertLess(abs(st.getTime() - time.time()), 3)
        rx = st.getRX()
        self.assertEqual(len(rx), 5)
        bar = st.getBarData()
        self.assertEqual(len(bar), 9)
        st.setTime()
        self.assertEqual(len(st.getStnTransmitters()), 8)

    def test_full_dump(self):
        st = self.station()
        got = list(st.genArchiveDump())
        self.assertEqual(len(got), N_RECORDS)

    def test_eeprom_read_from_console(self):
        """With a Vantage driver as the WeeWX console, the emulator copies its EEPROM.
        The 'console' here is the first emulator, with its setup bits changed to a
        0.01 in rain collector so the copy is distinguishable from a built-in image."""
        self.emulator.eeprom[0x2B] = 0xCD
        self.addCleanup(self.emulator.eeprom.__setitem__, 0x2B, 0xDD)
        st = self.station()

        class Engine(object):
            console = st

            def bind(self, event_type, callback):
                pass

        config = configobj.ConfigObj(self.config)
        config['WeatherLinkEmulator']['port'] = str(free_port())
        second = wlip_emulator.WeatherLinkEmulator(Engine(), config)
        self.addCleanup(second.shutDown)
        self.assertEqual(bytes(second.eeprom[:256]), bytes(self.emulator.eeprom[:256]))
        self.assertEqual(second.rain_encoder().name, '0.01 in')


if __name__ == '__main__':
    unittest.main()
