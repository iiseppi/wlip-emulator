# WLIP Emulator for WeeWX
# Version: 89 (Protocol-Compliant Edition)
#
# Changes in V89:
# - Fixed LOOP packet structure to match Davis protocol exactly (all 99 bytes)
# - Added missing fields: Extra Temps, Soil, Leaf, Alarms, Battery status
# - Fixed bar trend validation to use only valid Davis values
# - Corrected archive record packing for Rev B format
# - Added RXTEST, BARDATA, SETTIME, CLRLOG, NEWSETUP commands
# - Added LOOP2 packet support
# - Improved error handling throughout
# - Made WRD response configurable (VP2/Vue)

import socket
import threading
import struct
import logging
import time
import math
import datetime
import select
import os
import sys
import weewx
import weewx.units
import weewx.manager 
from weewx.engine import StdService

try:
    from weewx.drivers.vantage import FORECAST_STRINGS
except ImportError:
    FORECAST_STRINGS = ["Forecast not available"] * 200

log = logging.getLogger(__name__)

def logdbg(msg):
    log.debug("WLIP Emulator: %s" % msg)

def loginf(msg):
    log.info("WLIP Emulator: %s" % msg)

def logerr(msg):
    log.error("WLIP Emulator: %s" % msg)

def logcrit(msg):
    log.critical("WLIP Emulator: %s" % msg)

HARDWARE_RECORD_LIMIT = 2560

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
    """Calculate CRC-16 checksum per Davis protocol"""
    crc = 0
    for byte in data:
        crc = (crc << 8) ^ CRC_TABLE[((crc >> 8) ^ byte) & 0xFF]
        crc = crc & 0xFFFF
    return crc

class WeatherLinkEmulator(StdService):
    def __init__(self, engine, config_dict):
        super(WeatherLinkEmulator, self).__init__(engine, config_dict)
        options = config_dict.get('WeatherLinkEmulator', {})
        
        self.port = int(options.get('port', 22222))
        self.max_clients = int(options.get('max_clients', 10))
        
        # Station type: 16=VP2, 17=Vue
        self.station_type = int(options.get('station_type', 16))
        
        # LOGGING & DEBUG (0=Basic, 1=Stats/Lag, 2=Raw Hex)
        self.debug_detail = int(options.get('debug_detail', 0))

        # SOFT START
        self.startup_delay = int(options.get('startup_delay', 0))
        
        # WATCHDOG SETTINGS
        self.max_lag_threshold = int(options.get('max_lag_threshold', 0))
        # Action: 0=LogOnly, 1=DisconnectClient, 2=KillWeeWX
        self.max_lag_action = int(options.get('max_lag_action', 0))
        
        self.current_loop_packet = None
        self.lock = threading.Lock()
        self.engine = engine
        self.config_dict = config_dict 
        
        self.last_update_time = 0
        self.server_sockets = [] 
        self.active_connections = 0
        
        # Port mappings for VIP clients
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
                    loginf("Configured VIP mapping: IP %s -> Port %s" % (ip.strip(), p))
        except Exception as e:
            logerr("Error parsing client_mapping: %s" % e)
        
        if self.port not in self.port_mappings:
             self.port_mappings[self.port] = None
             loginf("Default Port %s is OPEN to all connections." % self.port)

        # Archive interval calculation with V88 fix
        interval_seconds = 300 
        std_archive = config_dict.get('StdArchive', {})
        if 'archive_interval' in std_archive:
            interval_seconds = int(std_archive['archive_interval'])
        if 'archive_interval' in options:
            val = int(options['archive_interval'])
            interval_seconds = val * 60
            
        calc_mins = int(interval_seconds / 60)
        # Clamp to 1-255 to prevent EEPROM overflow
        if calc_mins > 255:
            loginf("WARNING: Archive interval %d mins exceeds Davis limit. Clamping to 255." % calc_mins)
            self.davis_interval_mins = 255
        elif calc_mins < 1:
            self.davis_interval_mins = 1
        else:
            self.davis_interval_mins = calc_mins
        
        # EEPROM Configuration
        STATION_LATITUDE = 611
        STATION_LONGITUDE = 224
        STATION_TIME_ZONE = 23
        RAIN_COLLECTOR_TYPE = 0x01
        
        self.eeprom = bytearray(4096)  # Full 4K EEPROM
        
        # Station location
        struct.pack_into('<h', self.eeprom, 0x0B, STATION_LATITUDE)
        struct.pack_into('<h', self.eeprom, 0x0D, STATION_LONGITUDE)
        self.eeprom[0x11] = STATION_TIME_ZONE
        self.eeprom[0x12] = 0x00  # Auto DST
        
        # Setup bits
        self.eeprom[0x2B] = 0x10 | RAIN_COLLECTOR_TYPE
        
        # Archive interval
        self.eeprom[0x2D] = self.davis_interval_mins
        
        # Unit bits (F, inHg, mph, etc)
        self.eeprom[0x29] = 0x00
        self.eeprom[0x2A] = 0xFF
        
        self.db_binding = options.get('binding', 'wx_binding')
        
        loginf("*** WLIP EMULATOR V89 (Protocol-Compliant) ***")
        loginf("Station Type: %s | Archive: %d min | Watchdog: %ds/%d" % 
               ("Vue" if self.station_type == 17 else "VP2", 
                self.davis_interval_mins, self.max_lag_threshold, self.max_lag_action))
        
        self.run_server()
        self.bind(weewx.NEW_LOOP_PACKET, self.handle_new_loop)

    def shutDown(self):
        loginf("Shutting down WLIP Server...")
        try:
            for sock in self.server_sockets:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except:
                    pass
                sock.close()
            self.server_sockets = []
        except Exception as e:
            logerr("Error during shutdown: %s" % e)

    def handle_new_loop(self, event):
        with self.lock:
            self.current_loop_packet = event.packet
            self.last_update_time = time.time()
            
            if self.debug_detail >= 1:
                try:
                    ts = event.packet.get('dateTime')
                    t = event.packet.get('outTemp')
                    logdbg("INTERNAL UPDATE: WeeWX packet TS=%s Temp=%s" % (ts, t))
                except:
                    pass

    def run_server(self):
        t = threading.Thread(target=self._server_coordinator)
        t.daemon = True
        t.start()

    def _server_coordinator(self):
        if self.startup_delay > 0:
            loginf("Startup delay: Waiting %d seconds..." % self.startup_delay)
            time.sleep(self.startup_delay)
            loginf("Startup delay complete. Opening ports.")

        for port, allowed_ip in self.port_mappings.items():
            t = threading.Thread(target=self.listen_on_port, args=(port, allowed_ip))
            t.daemon = True
            t.start()

    def listen_on_port(self, port, allowed_ip):
        access_msg = "ALL" if allowed_ip is None else allowed_ip
        loginf("Listening on port %d [Allowed: %s]" % (port, access_msg))
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(None)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            sock.bind(('0.0.0.0', port))
            sock.listen(self.max_clients)
            with self.lock:
                self.server_sockets.append(sock)
            
            while True:
                try:
                    client_sock, addr = sock.accept()
                except OSError:
                    break
                
                if allowed_ip is not None and addr[0] != allowed_ip:
                    logdbg("Rejected connection from %s (not %s)" % (addr[0], allowed_ip))
                    client_sock.close()
                    continue
                
                client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                client_sock.settimeout(None)
                client_thread = threading.Thread(target=self.handle_client, args=(client_sock, addr))
                client_thread.daemon = True
                client_thread.start()
        except Exception as e:
            logerr("Error on port %d: %s" % (port, e))

    def handle_client(self, client_sock, addr):
        with self.lock:
            self.active_connections += 1
            count = self.active_connections
        
        try:
            local_port = client_sock.getsockname()[1]
        except:
            local_port = "Unknown"
            
        loginf("Connection from %s:%s -> Port %s. Active: %d" % 
               (addr[0], addr[1], local_port, count))

        buffer = bytearray()

        try:
            while True:
                data = client_sock.recv(1024)
                if not data: 
                    break
                
                if self.debug_detail >= 2:
                    hex_data = " ".join("{:02x}".format(c) for c in data)
                    if len(hex_data) < 100:
                        logdbg("RX [%s]: %s" % (addr[0], hex_data))
                    else:
                        logdbg("RX [%s]: %s..." % (addr[0], hex_data[:100]))
                
                buffer.extend(data)
                
                while len(buffer) > 0:
                    # Wake-up sequence
                    if buffer[0] == 0x0A or buffer[0] == 0x0D:
                        client_sock.sendall(b'\n\r')
                        buffer = buffer[1:]
                        continue
                    
                    # Command with line feed
                    if b'\n' in buffer:
                        split_idx = buffer.find(b'\n')
                        cmd_bytes = buffer[:split_idx]
                        buffer = buffer[split_idx+1:] 
                        cmd_str = cmd_bytes.decode('utf-8', errors='ignore').strip()
                        if len(cmd_str) > 0:
                            self.process_command(client_sock, cmd_str, cmd_bytes)
                        continue
                    
                    # Binary WRD command
                    if b'WRD' in buffer:
                         self.process_command(client_sock, "WRD", buffer)
                         buffer = bytearray() 
                         continue

                    break

        except Exception as e:
            if self.debug_detail >= 1:
                logdbg("Client disconnected: %s" % e)
        finally:
            client_sock.close()
            with self.lock:
                self.active_connections -= 1
                count = self.active_connections
            loginf("Disconnected %s. Active: %d" % (addr[0], count))

    def process_command(self, client_sock, cmd, raw_data):
        """Process incoming Davis commands"""
        try:
            # Mystery command (returns NAK per protocol)
            if b'\x12\x4d' in raw_data: 
                client_sock.sendall(b'\x21')
                return

            # Testing commands
            if 'TEST' in cmd:
                client_sock.sendall(b'\n\rTEST\n\r')
                
            elif 'WRD' in cmd:
                # Station type: 16=VP2, 17=Vue
                client_sock.sendall(b'\x06' + bytes([self.station_type]))
                
            elif 'RXTEST' in cmd:
                client_sock.sendall(b'\n\rOK\n\r')
                
            elif 'VER' in cmd:
                client_sock.sendall(b'\n\rOK\n\rMay  1 2012\n\r')
                
            elif 'NVER' in cmd:
                client_sock.sendall(b'\n\rOK\n\r1.90\n\r')
                
            elif 'RECEIVERS' in cmd:
                # Bit 0 = ID 1 (ISS)
                client_sock.sendall(b'\n\rOK\n\r\x01')
                
            # Time commands
            elif 'GETTIME' in cmd:
                self.handle_gettime(client_sock)
                
            elif 'SETTIME' in cmd:
                self.handle_settime(client_sock)
                
            # EEPROM commands
            elif 'EEBRD' in cmd:
                self.handle_eebrd(client_sock, cmd)
                
            elif 'EERD' in cmd:
                self.handle_eerd(client_sock, cmd)
                
            elif 'EEWR' in cmd:
                self.handle_eewr(client_sock, cmd)
                
            # Download commands
            elif 'DMPAFT' in cmd:
                self.handle_dmpaft(client_sock)
                
            elif 'DMP' in cmd:
                self.handle_dmp(client_sock)
                
            # Current data commands
            elif 'LOOP' in cmd:
                pkt_count = self._parse_count(cmd, 1)
                self.handle_loop(client_sock, pkt_count, loop_type=1)
                
            elif 'LPS' in cmd:
                pkt_count = self._parse_count(cmd, 1)
                # LPS supports LOOP (bit 0) and LOOP2 (bit 1)
                # For now, send LOOP packets
                self.handle_loop(client_sock, pkt_count, loop_type=1)
                
            elif 'HILOWS' in cmd:
                self.handle_hilows(client_sock)
                
            # Barometer commands
            elif 'BARDATA' in cmd:
                self.handle_bardata(client_sock)
                
            elif 'BARREAD' in cmd:
                payload = b'\x00\x00'
                crc = crc16(payload)
                client_sock.sendall(b'\x06' + payload + struct.pack('>H', crc))
                
            # Diagnostics
            elif 'RXCHECK' in cmd:
                client_sock.sendall(b'\n\rOK\n\r12000 5 0 2500 10\n\r')
                
            elif 'STR' in cmd:
                self.handle_str(client_sock)
                
            # Clearing commands
            elif 'CLRLOG' in cmd:
                client_sock.sendall(b'\x06')
                
            elif 'NEWSETUP' in cmd:
                client_sock.sendall(b'\x06')
                
            else:
                if self.debug_detail >= 1:
                    logdbg("Unknown command: %s" % cmd)
                    
        except Exception as e:
            logerr("Command '%s' failed: %s" % (cmd, e))
            try:
                client_sock.sendall(b'\x21')  # NAK
            except:
                pass

    def _parse_count(self, cmd, default=1):
        """Extract packet count from command"""
        try:
            parts = cmd.split()
            for p in parts:
                if p.isdigit():
                    count = int(p)
                    return count if count > 0 else default
        except:
            pass
        return default

    def handle_settime(self, client_sock):
        """Handle SETTIME command"""
        client_sock.sendall(b'\x06')
        try:
            data = client_sock.recv(8)  # 6 bytes + 2 CRC
            if len(data) == 8:
                # Verify CRC
                calc_crc = crc16(data[0:6])
                recv_crc = struct.unpack('>H', data[6:8])[0]
                if calc_crc == recv_crc:
                    client_sock.sendall(b'\x06')
                    logdbg("SETTIME: Time updated")
                else:
                    client_sock.sendall(b'\x18')  # CANCEL
        except:
            pass

    def handle_eewr(self, client_sock, cmd):
        """Handle EEWR command (write single EEPROM byte)"""
        try:
            parts = cmd.split()
            if len(parts) >= 3:
                addr = int(parts[1], 16)
                value = int(parts[2], 16)
                if addr < len(self.eeprom):
                    self.eeprom[addr] = value
                    client_sock.sendall(b'\n\rOK\n\r')
                    if self.debug_detail >= 1:
                        logdbg("EEWR: Set addr 0x%02X = 0x%02X" % (addr, value))
                else:
                    client_sock.sendall(b'\x21')
        except Exception as e:
            logdbg("EEWR Error: %s" % e)
            client_sock.sendall(b'\x21')

    def handle_eebrd(self, client_sock, cmd):
        """Handle EEBRD command (binary EEPROM read)"""
        try:
            parts = cmd.split()
            if len(parts) >= 3:
                addr = int(parts[1], 16)
                length = int(parts[2], 16)
                data_chunk = bytearray()
                
                for i in range(length):
                    curr_addr = addr + i
                    val = 0
                    if curr_addr < len(self.eeprom):
                        val = self.eeprom[curr_addr]
                    data_chunk.append(val)
                    
                crc = crc16(data_chunk)
                packet = data_chunk + struct.pack('>H', crc)
                client_sock.sendall(b'\x06' + packet)
        except Exception as e:
            logdbg("EEBRD Error: %s" % e)

    def handle_eerd(self, client_sock, cmd):
        """Handle EERD command (hex string EEPROM read)"""
        try:
            parts = cmd.split()
            if len(parts) >= 3:
                addr = int(parts[1], 16)
                length = int(parts[2], 16)
                
                client_sock.sendall(b'\n\rOK\n\r')
                
                for i in range(length):
                    curr_addr = addr + i
                    val = 0
                    if curr_addr < len(self.eeprom):
                        val = self.eeprom[curr_addr]
                    hex_str = "%02X\n\r" % val
                    client_sock.sendall(hex_str.encode('utf-8'))
        except Exception as e:
            logdbg("EERD Error: %s" % e)

    def handle_gettime(self, client_sock):
        """Handle GETTIME command"""
        now = datetime.datetime.now()
        payload = bytearray(6)
        payload[0] = now.second
        payload[1] = now.minute
        payload[2] = now.hour
        payload[3] = now.day
        payload[4] = now.month
        payload[5] = now.year - 1900
        crc = crc16(payload)
        packet = payload + struct.pack('>H', crc)
        client_sock.sendall(b'\x06' + packet)

    def handle_bardata(self, client_sock):
        """Handle BARDATA command"""
        with self.lock:
            p = self.current_loop_packet
        
        baro = 29920  # Default
        if p:
            baro_val = p.get('barometer')
            if baro_val:
                baro = int(baro_val * 1000)
        
        response = (
            b'\n\rOK\n\r'
            b'BAR %d\n\r' % baro +
            b'ELEVATION 0\n\r'
            b'DEW POINT 50\n\r'
            b'VIRTUAL TEMP 60\n\r'
            b'C 12\n\r'
            b'R 1000\n\r'
            b'BARCAL 0\n\r'
            b'GAIN 0\n\r'
            b'OFFSET 0\n\r'
        )
        client_sock.sendall(response)

    def handle_str(self, client_sock):
        """Handle STR command (forecast string)"""
        forecast_text = "Forecast not available"
        with self.lock:
            if self.current_loop_packet:
                rule = int(self.current_loop_packet.get('forecastRule', 0))
                if 0 <= rule < len(FORECAST_STRINGS):
                    forecast_text = FORECAST_STRINGS[rule]
                else:
                    forecast_text = "Forecast rule %s unknown" % rule
        resp = forecast_text.encode('utf-8') + b'\n\r'
        client_sock.sendall(resp)

    def handle_hilows(self, client_sock):
        """Handle HILOWS command (high/low values)"""
        # Return empty HILOWS packet (436 bytes of zeros)
        payload = b'\x00' * 436
        crc = crc16(payload)
        client_sock.sendall(b'\x06' + payload + struct.pack('>H', crc))

    def handle_loop(self, client_sock, count, loop_type=1):
        """Handle LOOP/LPS commands"""
        
        # Watchdog check
        lag_found = False
        lag_val = 0
        
        if self.debug_detail >= 1 or self.max_lag_threshold > 0:
            with self.lock:
                p = self.current_loop_packet
            
            info_str = "No Data"
            if p:
                try:
                    ts = p.get('dateTime', 0)
                    now = time.time()
                    lag_val = int(now - ts)
                    lag_found = True
                    
                    if self.debug_detail >= 1:
                        temp = p.get('outTemp')
                        wind = p.get('windSpeed')
                        t_str = "%.1f" % temp if temp is not None else "None"
                        w_str = "%.1f" % wind if wind is not None else "None"
                        info_str = "Lag: %ds | Temp: %s | Wind: %s" % (lag_val, t_str, w_str)
                except:
                    pass
            
            if self.debug_detail >= 1:
                logdbg("Sending %d LOOP packets... [%s]" % (count, info_str))

        # Watchdog action
        if self.max_lag_threshold > 0 and lag_found:
            if lag_val > self.max_lag_threshold:
                msg = "WATCHDOG: Data lag %ds > %ds threshold" % (lag_val, self.max_lag_threshold)
                
                if self.max_lag_action == 2:
                    logcrit(msg + " - KILLING WEEWX PROCESS")
                    try:
                        client_sock.close()
                    except:
                        pass
                    os._exit(1)
                    
                elif self.max_lag_action == 1:
                    logerr(msg + " - Disconnecting client")
                    try:
                        client_sock.close()
                    except:
                        pass
                    return

        try:
            client_sock.sendall(b'\x06')
        except:
            return

        for i in range(count):
            # Check for client interrupt
            try:
                ready_to_read, _, _ = select.select([client_sock], [], [], 0)
                if ready_to_read:
                    if self.debug_detail >= 1:
                        logdbg("Loop interrupted by client")
                    break
            except:
                break

            try:
                if loop_type == 2:
                    packed_data = self.create_davis_loop2_packet()
                else:
                    packed_data = self.create_davis_loop_packet()
                    
                client_sock.sendall(packed_data)
                
                if count > 1:
                    time.sleep(2.0)
                    
            except socket.error as e:
                logdbg("Send failed in loop: %s" % e)
                break
            except Exception as e:
                logerr("Error in loop: %s" % e)
                break

    def handle_dmp(self, client_sock):
        """Handle DMP command (download all archive)"""
        # Force full download by setting timestamp to zero
        client_sock.sendall(b'\x06')
        
        # Simulate zero timestamp sent from client
        limit_seconds = HARDWARE_RECORD_LIMIT * (self.davis_interval_mins * 60)
        requested_ts = int(time.time() - limit_seconds)
        
        self._download_archive(client_sock, requested_ts)

    def handle_dmpaft(self, client_sock):
        """Handle DMPAFT command (download after timestamp)"""
        client_sock.sendall(b'\x06')
        
        try:
            ts_data = client_sock.recv(6)
        except:
            return
            
        if len(ts_data) != 6: 
            return

        davis_date = struct.unpack('<H', ts_data[0:2])[0]
        davis_time = struct.unpack('<H', ts_data[2:4])[0]
        
        requested_ts = 0
        try:
            if davis_date == 0 and davis_time == 0:
                # Full download - hardware limit
                limit_seconds = HARDWARE_RECORD_LIMIT * (self.davis_interval_mins * 60)
                requested_ts = int(time.time() - limit_seconds)
                logdbg("DMPAFT: Full download requested (HW limit)")
            else:
                day = davis_date & 0x1F
                month = (davis_date >> 5) & 0x0F
                year = (davis_date >> 9) + 2000
                hour = int(davis_time / 100)
                minute = davis_time % 100
                dt = datetime.datetime(year, month, day, hour, minute)
                requested_ts = time.mktime(dt.timetuple())
                logdbg("DMPAFT: Requesting after %s" % dt)
        except Exception as e:
            limit_seconds = HARDWARE_RECORD_LIMIT * (self.davis_interval_mins * 60)
            requested_ts = int(time.time() - limit_seconds)
            logdbg("DMPAFT: Invalid timestamp, using HW limit")

        client_sock.sendall(b'\x06')
        self._download_archive(client_sock, requested_ts)

    def _download_archive(self, client_sock, requested_ts):
        """Common archive download logic"""
        records = []
        try:
            with weewx.manager.open_manager_with_config(self.config_dict, self.db_binding) as manager:
                for record in manager.genBatchRecords(requested_ts + 1):
                    records.append(record)
                    if len(records) >= 50000: 
                        break
            
            if self.debug_detail >= 1:
                if len(records) > 0:
                    first_ts = records[0].get('dateTime')
                    last_ts = records[-1].get('dateTime')
                    logdbg("Archive query: %d records [%s to %s]" % 
                           (len(records), first_ts, last_ts))
                else:
                    logdbg("Archive query: 0 records found")
            
        except Exception as e:
            logerr("Archive DB error: %s" % e)
            records = []
        
        num_records = len(records)
        num_pages = int(math.ceil(num_records / 5.0))
        
        # Send header
        header = struct.pack('<H', num_pages) + b'\x00\x00'
        header_crc = crc16(header)
        client_sock.sendall(header + struct.pack('>H', header_crc))

        try:
            ack = client_sock.recv(1)
            if ack != b'\x06': 
                return
        except:
            return

        # Send pages
        for page_idx in range(num_pages):
            page_buffer = bytearray()
            page_buffer.append(page_idx % 256)
            
            for i in range(5):
                rec_idx = page_idx * 5 + i
                if rec_idx < num_records:
                    page_buffer.extend(self.pack_archive_record(records[rec_idx]))
                else:
                    page_buffer.extend(b'\xff' * 52)
                    
            page_buffer.extend(b'\x00\x00\x00\x00')  # Unused bytes
            pg_crc = crc16(page_buffer)
            page_buffer.extend(struct.pack('>H', pg_crc))
            
            client_sock.sendall(page_buffer)
            
            try:
                ack = client_sock.recv(1)
                if ack == b'\x1B':  # ESC
                    logdbg("Download cancelled by client")
                    break
            except:
                break

    def pack_archive_record(self, record):
        """Pack archive record in Rev B format (52 bytes)"""
        rec_us = weewx.units.to_std_system(record, weewx.US)
        
        ts = rec_us['dateTime']
        t = time.localtime(ts)
        davis_date = t.tm_mday + (t.tm_mon * 32) + ((t.tm_year - 2000) * 512)
        davis_time = (t.tm_hour * 100) + t.tm_min
        
        packet = bytearray(52)
        
        # Date and time stamps
        struct.pack_into('<H', packet, 0, davis_date)
        struct.pack_into('<H', packet, 2, davis_time)
        
        def get(key, scale=1, offset=0, dash=0):
            val = rec_us.get(key)
            if val is None: 
                return dash
            return int((val * scale) + offset)
        
        # Temperatures (offset 4-9)
        struct.pack_into('<h', packet, 4, get('outTemp', 10, 0, 32767))
        struct.pack_into('<h', packet, 6, get('outTemp', 10, 0, -32768))  # High
        struct.pack_into('<h', packet, 8, get('outTemp', 10, 0, 32767))   # Low
        
        # Rain (offset 10-13)
        struct.pack_into('<H', packet, 10, get('rain', 100, 0, 0))
        struct.pack_into('<H', packet, 12, get('rainRate', 100, 0, 0))
        
        # Barometer (offset 14-15)
        baro = get('barometer', 1000, 0, 0)
        if baro == 0: 
            baro = 29920
        struct.pack_into('<H', packet, 14, baro)
        
        # Solar radiation (offset 16-17)
        struct.pack_into('<H', packet, 16, get('radiation', 1, 0, 32767))
        
        # Wind samples (offset 18-19)
        struct.pack_into('<H', packet, 18, 100)
        
        # Inside temp/humidity (offset 20-23)
        struct.pack_into('<h', packet, 20, get('inTemp', 10, 0, 32767))
        packet[22] = get('inHumidity', 1, 0, 255)
        packet[23] = get('outHumidity', 1, 0, 255)
        
        # Wind (offset 24-27)
        packet[24] = get('windSpeed', 1, 0, 255)
        packet[25] = get('windGust', 1, 0, 0)
        
        # Wind direction code
        wind_dir = rec_us.get('windDir')
        if wind_dir is not None:
            dir_code = int((wind_dir / 22.5) + 0.5) % 16
        else:
            dir_code = 255
        packet[26] = dir_code  # High wind dir
        packet[27] = dir_code  # Prevailing wind dir
        
        # UV and ET (offset 28-29)
        packet[28] = get('UV', 10, 0, 255)
        packet[29] = get('ET', 1000, 0, 0)
        
        # Rev B specific fields (offset 30-41)
        struct.pack_into('<H', packet, 30, get('radiation', 1, 0, 0))  # High solar
        packet[32] = get('UV', 10, 0, 0)  # High UV
        packet[33] = get('forecastRule', 1, 0, 193)  # Forecast rule
        
        # Leaf temps (offset 34-35)
        packet[34] = 0xFF
        packet[35] = 0xFF
        
        # Leaf wetness (offset 36-37)
        packet[36] = 0xFF
        packet[37] = 0xFF
        
        # Soil temps (offset 38-41)
        for i in range(38, 42):
            packet[i] = 0xFF
        
        # Record type (offset 42) - 0x00 = Rev B
        packet[42] = 0x00
        
        # Extra humidity (offset 43-44)
        packet[43] = 0xFF
        packet[44] = 0xFF
        
        # Extra temps (offset 45-47)
        for i in range(45, 48):
            packet[i] = 0xFF
        
        # Soil moisture (offset 48-51)
        for i in range(48, 52):
            packet[i] = 0xFF
        
        return packet

    def create_davis_loop_packet(self):
        """Create LOOP packet (99 bytes) per Davis protocol"""
        with self.lock:
            if self.current_loop_packet:
                p_raw = self.current_loop_packet.copy()
            else:
                p_raw = None

        if not p_raw:
            p_raw = {'dateTime': int(time.time()), 'usUnits': weewx.US}

        if p_raw.get('usUnits') != weewx.US:
            try:
                p = weewx.units.to_std_system(p_raw, weewx.US)
            except:
                p = p_raw
        else:
            p = p_raw

        def get_val(key, conversion_func=float, fallback=0):
            try:
                val = p.get(key)
                if val is not None:
                    return conversion_func(val)
            except:
                pass
            return fallback

        # Extract values
        outTemp = get_val('outTemp')
        inTemp = get_val('inTemp')
        barometer = get_val('barometer')
        windSpeed = get_val('windSpeed')
        windDir = get_val('windDir', int, 0)
        rainRate = get_val('rainRate')
        outHumidity = get_val('outHumidity')
        inHumidity = get_val('inHumidity')
        dayRain = get_val('dayRain')
        monthRain = get_val('monthRain')
        yearRain = get_val('yearRain')
        uv = get_val('UV', float, 0)
        radiation = get_val('radiation', float, 0)
        forecast_rule = get_val('forecastRule', int, 0)
        bar_trend = get_val('barometerTrend', int, 0)
        sunrise_epoch = get_val('sunrise', int, 0)
        sunset_epoch = get_val('sunset', int, 0)

        # Convert sunrise/sunset
        def to_davis_time(epoch):
            if not epoch: 
                return 0
            t = time.localtime(epoch)
            return t.tm_hour * 100 + t.tm_min
            
        sunrise_davis = to_davis_time(sunrise_epoch)
        sunset_davis = to_davis_time(sunset_epoch)

        # Create packet
        packet = bytearray(99)
        
        # Header (offset 0-3)
        struct.pack_into('<3s', packet, 0, b'LOO')
        
        # Bar trend (offset 3) - validate to Davis values
        TREND_MAP = {-2: 196, -1: 236, 0: 0, 1: 20, 2: 60}  # Unsigned byte values
        trend_val = TREND_MAP.get(bar_trend, 0)
        packet[3] = trend_val
        
        # Packet type (offset 4) - 0 = LOOP
        packet[4] = 0
        
        # Next record (offset 5-6)
        struct.pack_into('<H', packet, 5, 0)
        
        # Barometer (offset 7-8)
        bar_val = int(barometer * 1000) if barometer else 29920
        struct.pack_into('<H', packet, 7, bar_val)
        
        # Inside temp/humidity (offset 9-11)
        struct.pack_into('<h', packet, 9, int(inTemp * 10) if inTemp else 0)
        packet[11] = int(inHumidity) if inHumidity else 0
        
        # Outside temp (offset 12-13)
        struct.pack_into('<h', packet, 12, int(outTemp * 10) if outTemp else 0)
        
        # Wind speed (offset 14-15)
        ws_val = int(windSpeed) if windSpeed else 0
        packet[14] = ws_val
        packet[15] = ws_val  # 10-min avg
        
        # Wind direction (offset 16-17)
        struct.pack_into('<H', packet, 16, int(windDir))
        
        # Extra temps (offset 18-24) - 7 bytes, dashed
        for i in range(18, 25):
            packet[i] = 0xFF
        
        # Soil temps (offset 25-28) - 4 bytes, dashed
        for i in range(25, 29):
            packet[i] = 0xFF
        
        # Leaf temps (offset 29-32) - 4 bytes, dashed
        for i in range(29, 33):
            packet[i] = 0xFF
        
        # Outside humidity (offset 33)
        packet[33] = int(outHumidity) if outHumidity else 0
        
        # Extra humidities (offset 34-40) - 7 bytes, dashed
        for i in range(34, 41):
            packet[i] = 0xFF
        
        # Rain rate (offset 41-42)
        struct.pack_into('<H', packet, 41, int(rainRate * 100) if rainRate else 0)
        
        # UV (offset 43)
        uv_val = int(uv * 10) if uv < 25.5 else 255
        packet[43] = uv_val
        
        # Solar radiation (offset 44-45)
        struct.pack_into('<H', packet, 44, int(radiation))
        
        # Storm rain (offset 46-47)
        struct.pack_into('<H', packet, 46, 0)
        
        # Storm start date (offset 48-49)
        struct.pack_into('<H', packet, 48, 0)
        
        # Day/Month/Year rain (offset 50-55)
        struct.pack_into('<H', packet, 50, int(dayRain * 100) if dayRain else 0)
        struct.pack_into('<H', packet, 52, int(monthRain * 100) if monthRain else 0)
        struct.pack_into('<H', packet, 54, int(yearRain * 100) if yearRain else 0)
        
        # Day/Month/Year ET (offset 56-61)
        struct.pack_into('<H', packet, 56, 0)  # Day ET
        struct.pack_into('<H', packet, 58, 0)  # Month ET
        struct.pack_into('<H', packet, 60, 0)  # Year ET
        
        # Soil moistures (offset 62-65) - 4 bytes, dashed
        for i in range(62, 66):
            packet[i] = 0xFF
        
        # Leaf wetnesses (offset 66-69) - 4 bytes, dashed
        for i in range(66, 70):
            packet[i] = 0xFF
        
        # Alarms (offset 70-85) - all off
        for i in range(70, 86):
            packet[i] = 0x00
        
        # Transmitter battery (offset 86)
        packet[86] = 0x00
        
        # Console battery voltage (offset 87-88)
        struct.pack_into('<H', packet, 87, 0)
        
        # Forecast icon (offset 89)
        packet[89] = 0x00
        
        # Forecast rule (offset 90)
        packet[90] = forecast_rule
        
        # Sunrise/sunset (offset 91-94)
        struct.pack_into('<H', packet, 91, sunrise_davis)
        struct.pack_into('<H', packet, 93, sunset_davis)
        
        # Line terminators (offset 95-96)
        packet[95] = 0x0A  # \n
        packet[96] = 0x0D  # \r
        
        # CRC (offset 97-98) - calculated on bytes 0-96
        crc = crc16(packet[0:97])
        struct.pack_into('>H', packet, 97, crc)
        
        return bytes(packet)

    def create_davis_loop2_packet(self):
        """Create LOOP2 packet (99 bytes) per Davis protocol"""
        with self.lock:
            if self.current_loop_packet:
                p_raw = self.current_loop_packet.copy()
            else:
                p_raw = None

        if not p_raw:
            p_raw = {'dateTime': int(time.time()), 'usUnits': weewx.US}

        if p_raw.get('usUnits') != weewx.US:
            try:
                p = weewx.units.to_std_system(p_raw, weewx.US)
            except:
                p = p_raw
        else:
            p = p_raw

        def get_val(key, conversion_func=float, fallback=0):
            try:
                val = p.get(key)
                if val is not None:
                    return conversion_func(val)
            except:
                pass
            return fallback

        # Extract values
        outTemp = get_val('outTemp')
        inTemp = get_val('inTemp')
        barometer = get_val('barometer')
        windSpeed = get_val('windSpeed')
        windDir = get_val('windDir', int, 0)
        rainRate = get_val('rainRate')
        outHumidity = get_val('outHumidity')
        inHumidity = get_val('inHumidity')
        dayRain = get_val('dayRain')
        uv = get_val('UV', float, 0)
        radiation = get_val('radiation', float, 0)
        bar_trend = get_val('barometerTrend', int, 0)
        dewpoint = get_val('dewpoint', float, 0)
        windchill = get_val('windchill', float, 0)
        heatindex = get_val('heatindex', float, 0)

        packet = bytearray(99)
        
        # Header
        struct.pack_into('<3s', packet, 0, b'LOO')
        
        # Bar trend
        TREND_MAP = {-2: 196, -1: 236, 0: 0, 1: 20, 2: 60}
        packet[3] = TREND_MAP.get(bar_trend, 0)
        
        # Packet type (1 = LOOP2)
        packet[4] = 1
        
        # Unused (offset 5-6)
        struct.pack_into('<H', packet, 5, 0x7FFF)
        
        # Barometer
        bar_val = int(barometer * 1000) if barometer else 29920
        struct.pack_into('<H', packet, 7, bar_val)
        
        # Inside temp/humidity
        struct.pack_into('<h', packet, 9, int(inTemp * 10) if inTemp else 0)
        packet[11] = int(inHumidity) if inHumidity else 0
        
        # Outside temp
        struct.pack_into('<h', packet, 12, int(outTemp * 10) if outTemp else 0)
        
        # Wind speed
        packet[14] = int(windSpeed) if windSpeed else 0
        
        # Unused
        packet[15] = 0xFF
        
        # Wind direction
        struct.pack_into('<H', packet, 16, int(windDir))
        
        # Wind averages (10min, 2min) and gust
        struct.pack_into('<H', packet, 18, int(windSpeed * 10) if windSpeed else 0)
        struct.pack_into('<H', packet, 20, int(windSpeed * 10) if windSpeed else 0)
        struct.pack_into('<H', packet, 22, int(windSpeed * 10) if windSpeed else 0)
        struct.pack_into('<H', packet, 24, int(windDir))
        
        # Unused fields
        struct.pack_into('<H', packet, 26, 0x7FFF)
        struct.pack_into('<H', packet, 28, 0x7FFF)
        
        # Dewpoint
        struct.pack_into('<h', packet, 30, int(dewpoint) if dewpoint else 255)
        
        # Unused
        packet[32] = 0xFF
        
        # Outside humidity
        packet[33] = int(outHumidity) if outHumidity else 0
        
        # Unused
        packet[34] = 0xFF
        
        # Heat index
        struct.pack_into('<h', packet, 35, int(heatindex) if heatindex else 255)
        
        # Wind chill
        struct.pack_into('<h', packet, 37, int(windchill) if windchill else 255)
        
        # THSW (unused)
        struct.pack_into('<h', packet, 39, 255)
        
        # Rain rate
        struct.pack_into('<H', packet, 41, int(rainRate * 100) if rainRate else 0)
        
        # UV
        packet[43] = int(uv * 10) if uv < 25.5 else 255
        
        # Solar radiation
        struct.pack_into('<H', packet, 44, int(radiation))
        
        # Storm rain and date (unused)
        struct.pack_into('<H', packet, 46, 0)
        struct.pack_into('<H', packet, 48, 0)
        
        # Day rain
        struct.pack_into('<H', packet, 50, int(dayRain * 100) if dayRain else 0)
        
        # 15min, hour, 24hr rain (unused)
        struct.pack_into('<H', packet, 52, 0)
        struct.pack_into('<H', packet, 54, 0)
        
        # Day ET
        struct.pack_into('<H', packet, 56, 0)
        
        # 24hr rain
        struct.pack_into('<H', packet, 58, 0)
        
        # Bar reduction method
        packet[60] = 2  # NOAA
        
        # Bar offset and calibration
        struct.pack_into('<H', packet, 61, 0)
        struct.pack_into('<H', packet, 63, 0)
        struct.pack_into('<H', packet, 65, int(barometer * 1000) if barometer else 29920)
        struct.pack_into('<H', packet, 67, int(barometer * 1000) if barometer else 29920)
        struct.pack_into('<H', packet, 69, int(barometer * 1000) if barometer else 29920)
        
        # Unused fields (71-94)
        for i in range(71, 95):
            packet[i] = 0xFF
        
        # Line terminators
        packet[95] = 0x0A
        packet[96] = 0x0D
        
        # CRC
        crc = crc16(packet[0:97])
        struct.pack_into('>H', packet, 97, crc)
        
        return bytes(packet)