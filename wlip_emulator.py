# WLIP Emulator for WeeWX
# Version: 83
#
# Changes:
# - V83: Fixed 'client_mapping' parsing crash when multiple clients are defined (list vs string).
# - V83: Updated DMPAFT logic to emulate real Davis Logger memory limits (Hardware Record Limit).
# - V83: Added 'select' to handle_loop to detect client interrupts (fixes b'LO' error).
# - V82: Added logging for connection target port info.
# - V81: Logic update to ensure Default Port remains open alongside VIP Ports.
# - V80: Added dynamic IP-to-Port mapping via config file.
# - V79: Added initial Dual Port support.
# - V78: Implemented Ring Buffer to fix command dropping (CRITICAL FIX).
# - V77: Fixed sticky packet handling with byte-by-byte check.
# - V74: Added active connection logging.
# - V65: Implemented Smart Identity (IP Filtering) for Legacy PC support.
# - V63: Added NACK response for Mystery Command (0x12 0x4d).
# - V57-V58: Implemented aggressive wake-up timing for sluggish consoles.
# - V50: Added shutDown() to prevent "Address already in use" on restarts.
# - V50: Added stale data check (>120s). Stops sending LOOP data if WeeWX/Station is down.
# - V50: HISTORY catch-up logic extended to 30 days & buffer increased to 50,000 records.
# - V50: FIXED Archive Interval reporting at EEPROM address 0x2D.


import socket
import threading
import struct
import logging
import time
import math
import datetime
import select  # Required for interrupt detection
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
        self.current_loop_packet = None
        self.lock = threading.Lock()
        self.engine = engine
        self.config_dict = config_dict 
        
        self.last_update_time = 0
        self.server_sockets = [] 
        self.active_connections = 0
        
        # --- PORT MAPPING PARSING (FIXED) ---
        self.port_mappings = {}
        raw_mapping = options.get('client_mapping', '')
        
        # Convert to list if it's a string, or use as list if configobj parsed it as such
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
        
        # Ensure Default Port is OPEN
        if self.port not in self.port_mappings:
             self.port_mappings[self.port] = None
             loginf("Default Port %s is OPEN to all connections." % self.port)

        interval_seconds = 300 
        std_archive = config_dict.get('StdArchive', {})
        if 'archive_interval' in std_archive:
            interval_seconds = int(std_archive['archive_interval'])
        if 'archive_interval' in options:
            val = int(options['archive_interval'])
            interval_seconds = val * 60
        self.davis_interval_mins = max(1, int(interval_seconds / 60))
        
        # USER SETTINGS 
        STATION_LATITUDE = 611
        STATION_LONGITUDE = 224
        STATION_TIME_ZONE = 23
        RAIN_COLLECTOR_TYPE = 0x01
        
        # EEPROM SETUP
        self.eeprom = bytearray(256)
        struct.pack_into('<h', self.eeprom, 0x0B, STATION_LATITUDE)
        struct.pack_into('<h', self.eeprom, 0x0D, STATION_LONGITUDE)
        self.eeprom[0x12] = STATION_TIME_ZONE
        self.eeprom[0x2B] = 0x10 | RAIN_COLLECTOR_TYPE
        self.eeprom[0x2D] = self.davis_interval_mins
        self.eeprom[0x2E] = 1
        
        self.db_binding = options.get('binding', 'wx_binding')
        
        loginf("*** WLIP EMULATOR V83 (Interruptible Loop) ***")
        
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

    def run_server(self):
        for port, allowed_ip in self.port_mappings.items():
            t = threading.Thread(target=self.listen_on_port, args=(port, allowed_ip))
            t.daemon = True
            t.start()

    def listen_on_port(self, port, allowed_ip):
        access_msg = "ALL (Default)" if allowed_ip is None else allowed_ip
        loginf("Starting listener on port %d [Allowed: %s]" % (port, access_msg))
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(None)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            sock.bind(('0.0.0.0', port))
            sock.listen(self.max_clients)
            self.server_sockets.append(sock)
            
            while True:
                try:
                    client_sock, addr = sock.accept()
                except OSError:
                    break
                
                if allowed_ip is not None and addr[0] != allowed_ip:
                    # Silently drop unauthorized connections on VIP ports
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
            
        loginf("Incoming connection from %s to PORT %s. Active: %d" % (str(addr), str(local_port), count))

        buffer = bytearray()

        try:
            while True:
                data = client_sock.recv(1024)
                if not data: break
                
                buffer.extend(data)
                
                while len(buffer) > 0:
                    # Clean up random newlines
                    if buffer[0] == 0x0A or buffer[0] == 0x0D:
                        client_sock.sendall(b'\n\r')
                        buffer = buffer[1:]
                        continue
                    
                    if b'\n' in buffer:
                        split_idx = buffer.find(b'\n')
                        cmd_bytes = buffer[:split_idx]
                        buffer = buffer[split_idx+1:] 
                        cmd_str = cmd_bytes.decode('utf-8', errors='ignore').strip()
                        if len(cmd_str) > 0:
                            self.process_command(client_sock, cmd_str, cmd_bytes)
                        continue
                    
                    # Davis Wakeup 'WRD' often comes without newline
                    if b'WRD' in buffer:
                         self.process_command(client_sock, "WRD", buffer)
                         buffer = bytearray() 
                         continue

                    break

        except Exception as e:
            logdbg("Client connection closed/error: %s" % e)
        finally:
            client_sock.close()
            with self.lock:
                self.active_connections -= 1
                count = self.active_connections
            loginf("Connection closed from %s. Active: %d" % (str(addr), count))

    def process_command(self, client_sock, cmd, raw_data):
        if b'\x12\x4d\x0d' in raw_data: 
            client_sock.sendall(b'\x21') 
            return

        if 'TEST' in cmd:
            client_sock.sendall(b'\n\rTEST\n\r')
        elif 'WRD' in cmd:
            client_sock.sendall(b'\x06\x11') # Vantage Pro 2 ID
        elif 'VVPVER' in cmd:
            client_sock.sendall(b'\n\rOK\n\r')
        elif 'GETTIME' in cmd:
            self.handle_gettime(client_sock)
        elif 'NVER' in cmd:
            client_sock.sendall(b'\n\rOK\n\r1.90\n\r')
        elif 'VER' in cmd:
            client_sock.sendall(b'\n\rOK\n\rMay  1 2012\n\r')
        elif 'EEBRD' in cmd:
            self.handle_eebrd(client_sock, cmd)
        elif 'EERD' in cmd:
            self.handle_eerd(client_sock, cmd)
        elif 'DMPAFT' in cmd:
            self.handle_dmpaft(client_sock)
        elif 'STR' in cmd:
            self.handle_str(client_sock)
        elif 'LOOP' in cmd:
            pkt_count = 1
            try:
                parts = cmd.split()
                for p in parts:
                    if p.isdigit():
                        pkt_count = int(p)
                        break
            except:
                pkt_count = 1
            if pkt_count == 0: pkt_count = 1 
            self.handle_loop(client_sock, pkt_count)
        elif 'LPS' in cmd:
            pkt_count = 1
            try:
                parts = cmd.split()
                for p in parts:
                    if p.isdigit():
                        pkt_count = int(p)
                        break
            except:
                pkt_count = 1
            if pkt_count == 0: pkt_count = 1
            self.handle_loop(client_sock, pkt_count)
        elif 'BARREAD' in cmd:
            payload = b'\x00\x00' 
            crc = crc16(payload)
            client_sock.sendall(b'\x06' + payload + struct.pack('>H', crc))
        elif 'HILOWS' in cmd:
            payload = b'\x00' * 436
            crc = crc16(payload)
            client_sock.sendall(b'\x06' + payload + struct.pack('>H', crc))
        elif 'RXCHECK' in cmd:
            payload = b'\x00\x00\x00\x00\x00'
            crc = crc16(payload)
            client_sock.sendall(b'\x06' + payload + struct.pack('>H', crc))

    def handle_eebrd(self, sock, cmd):
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
                sock.sendall(b'\x06' + packet)
        except Exception as e:
            logdbg("EEBRD Error: %s" % e)

    def handle_eerd(self, sock, cmd):
        try:
            parts = cmd.split()
            if len(parts) >= 3:
                addr = int(parts[1], 16)
                length = int(parts[2], 16)
                hex_str = ""
                for i in range(length):
                    curr_addr = addr + i
                    val = 0
                    if curr_addr < len(self.eeprom):
                        val = self.eeprom[curr_addr]
                    hex_str += "%02X" % val
                resp = hex_str.encode('utf-8') + b'\n\r' 
                sock.sendall(b'\x06' + resp)
        except Exception as e:
            logdbg("EERD Error: %s" % e)

    def handle_gettime(self, client_sock):
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
        logdbg("GETTIME requested. Sending: %s" % now)
        client_sock.sendall(b'\x06' + packet)

    def handle_str(self, client_sock):
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

    def handle_loop(self, client_sock, count):
        logdbg("Sending %d LOOP packets..." % count)
        try:
            client_sock.sendall(b'\x06')
        except:
            return

        for i in range(count):
            # V83: Interrupt Check
            try:
                ready_to_read, _, _ = select.select([client_sock], [], [], 0)
                if ready_to_read:
                    logdbg("Loop interrupted by client activity. Stopping stream.")
                    break
            except Exception:
                break

            try:
                packed_data = self.create_davis_loop_packet()
                client_sock.sendall(packed_data)
                
                if count > 1:
                    time.sleep(2.0)
                    
            except socket.error as e:
                logdbg("Send failed during loop stream: %s" % e)
                break
            except Exception as e:
                logdbg("Error in loop stream: %s" % e)
                break

    def handle_dmpaft(self, client_sock):
        client_sock.sendall(b'\x06')
        try:
            ts_data = client_sock.recv(6)
        except:
            return
        if len(ts_data) != 6: return

        davis_date = struct.unpack('<H', ts_data[0:2])[0]
        davis_time = struct.unpack('<H', ts_data[2:4])[0]
        
        requested_ts = 0
        try:
            if davis_date == 0 and davis_time == 0:
                limit_seconds = HARDWARE_RECORD_LIMIT * (self.davis_interval_mins * 60)
                requested_ts = int(time.time() - limit_seconds)
                logdbg("HISTORY REQUEST: Zero TS detected. Emulating HW limit: last %d records (%.1f hours) TS=%s" % 
                       (HARDWARE_RECORD_LIMIT, limit_seconds / 3600.0, requested_ts))
            else:
                day = davis_date & 0x1F
                month = (davis_date >> 5) & 0x0F
                year = (davis_date >> 9) + 2000
                hour = int(davis_time / 100)
                minute = davis_time % 100
                dt = datetime.datetime(year, month, day, hour, minute)
                requested_ts = time.mktime(dt.timetuple())
                logdbg("HISTORY REQUEST: Asking for data after: %s (%s)" % (dt, requested_ts))
        except Exception as e:
            limit_seconds = HARDWARE_RECORD_LIMIT * (self.davis_interval_mins * 60)
            requested_ts = int(time.time() - limit_seconds)
            logdbg("HISTORY REQUEST: Invalid timestamp decode. Defaulting to HW limit.")

        client_sock.sendall(b'\x06')

        records = []
        try:
            with weewx.manager.open_manager_with_config(self.config_dict, self.db_binding) as manager:
                for record in manager.genBatchRecords(requested_ts + 1):
                    records.append(record)
                    if len(records) >= 50000: break 
            
            logdbg("DB DEBUG: Found %d records to send." % len(records))
            
            if len(records) > 0:
                first_rec = records[0]
                loginf("DB DEBUG RECORD [0]: Time=%s outTemp=%s" % (
                    first_rec.get('dateTime'), 
                    first_rec.get('outTemp')
                ))
            
        except Exception as e:
            logerr("DB ERROR: Failed to query database: %s" % e)
            records = []
        
        num_records = len(records)
        num_pages = math.ceil(num_records / 5.0)
        
        header = struct.pack('<H', num_pages) + b'\x00\x00'
        header_crc = crc16(header)
        client_sock.sendall(header + struct.pack('>H', header_crc))

        try:
            ack = client_sock.recv(1)
            if ack != b'\x06': 
                return
        except:
            return

        if num_records > 0:
            logdbg("HISTORY: Sending %d pages..." % num_pages)

        for page_idx in range(num_pages):
            page_buffer = bytearray()
            page_buffer.append(page_idx % 256)
            for i in range(5):
                rec_idx = page_idx * 5 + i
                if rec_idx < num_records:
                    page_buffer.extend(self.pack_archive_record(records[rec_idx]))
                else:
                    page_buffer.extend(b'\xff' * 52)
            page_buffer.extend(b'\x00\x00\x00\x00')
            pg_crc = crc16(page_buffer)
            page_buffer.extend(struct.pack('>H', pg_crc))
            
            client_sock.sendall(page_buffer)
            
            try:
                ack = client_sock.recv(1)
                if ack == b'\x1B': 
                    break
            except:
                break
        
        logdbg("HISTORY: Sequence complete.")

    def pack_archive_record(self, record):
        rec_us = weewx.units.to_std_system(record, weewx.US)
        ts = rec_us['dateTime']
        t = time.localtime(ts)
        davis_date = t.tm_mday + (t.tm_mon * 32) + ((t.tm_year - 2000) * 512)
        davis_time = (t.tm_hour * 100) + t.tm_min
        packet = bytearray(52)
        struct.pack_into('<H', packet, 0, davis_date)
        struct.pack_into('<H', packet, 2, davis_time)
        def get(key, scale=1, offset=0, dash=0):
            val = rec_us.get(key)
            if val is None: return dash
            return int((val * scale) + offset)
        struct.pack_into('<h', packet, 4, get('outTemp', 10, 0, 32767))
        struct.pack_into('<h', packet, 6, get('outTemp', 10, 0, -32768)) 
        struct.pack_into('<h', packet, 8, get('outTemp', 10, 0, 32767))
        struct.pack_into('<H', packet, 10, get('rain', 100, 0, 0))
        struct.pack_into('<H', packet, 12, get('rainRate', 100, 0, 0))
        baro = get('barometer', 1000, 0, 0)
        if baro == 0: baro = 29920
        struct.pack_into('<H', packet, 14, baro)
        struct.pack_into('<H', packet, 16, get('radiation', 1, 0, 32767))
        struct.pack_into('<H', packet, 18, 100) 
        struct.pack_into('<h', packet, 20, get('inTemp', 10, 0, 32767))
        packet[22] = get('inHumidity', 1, 0, 255)
        packet[23] = get('outHumidity', 1, 0, 255)
        packet[24] = get('windSpeed', 1, 0, 255)
        packet[25] = get('windGust', 1, 0, 0)
        wind_dir = rec_us.get('windDir')
        if wind_dir is not None:
            dir_code = int((wind_dir / 22.5) + 0.5) % 16
        else:
            dir_code = 255 
        packet[26] = dir_code
        packet[27] = dir_code
        packet[28] = get('UV', 10, 0, 255)
        packet[29] = get('ET', 1000, 0, 0)
        struct.pack_into('<H', packet, 30, get('radiation', 1, 0, 32767))
        packet[32] = get('UV', 10, 0, 0) 
        packet[33] = 0 
        for i in range(34, 42): packet[i] = 0xFF 
        packet[42] = 0 
        for i in range(43, 52): packet[i] = 0xFF
        return packet

    def create_davis_loop_packet(self):
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
        monthRain = get_val('monthRain', float, 0)
        yearRain = get_val('yearRain', float, 0)
        forecast_rule = get_val('forecastRule', int, 0)
        bar_trend = get_val('barometerTrend', int, 0)
        sunrise_epoch = get_val('sunrise', int, 0)
        sunset_epoch = get_val('sunset', int, 0)

        sunrise_davis = 0
        sunset_davis = 0
        def to_davis_time(epoch):
            if not epoch: return 0
            t = time.localtime(epoch)
            return t.tm_hour * 100 + t.tm_min
        if sunrise_epoch: sunrise_davis = to_davis_time(sunrise_epoch)
        if sunset_epoch: sunset_davis = to_davis_time(sunset_epoch)

        packet = bytearray(99)
        struct.pack_into('<3s', packet, 0, b'LOO')
        try:
            struct.pack_into('<b', packet, 3, int(bar_trend))
        except:
            packet[3] = 0
        
        packet[4] = 0 
            
        struct.pack_into('<H', packet, 5, 0)
        bar_val = int(barometer * 1000) if barometer else 0
        if bar_val == 0: bar_val = 29920 
        struct.pack_into('<H', packet, 7, bar_val)
        struct.pack_into('<h', packet, 9, int(inTemp * 10) if inTemp else 0)
        packet[11] = int(inHumidity) if inHumidity else 0
        struct.pack_into('<h', packet, 12, int(outTemp * 10) if outTemp else 0)
        
        ws_val = int(windSpeed) if windSpeed else 0
        packet[14] = ws_val
        packet[15] = ws_val 
        
        struct.pack_into('<H', packet, 16, int(windDir))
        packet[33] = int(outHumidity) if outHumidity else 0
        struct.pack_into('<H', packet, 41, int(rainRate * 100) if rainRate else 0)
        packet[43] = int(uv * 10) if uv < 25.5 else 255 
        struct.pack_into('<H', packet, 44, int(radiation))
        struct.pack_into('<H', packet, 50, int(dayRain * 100) if dayRain else 0)
        struct.pack_into('<H', packet, 52, int(monthRain * 100) if monthRain else 0)
        struct.pack_into('<H', packet, 54, int(yearRain * 100) if yearRain else 0)
        struct.pack_into('<H', packet, 88, 800)
        packet[89] = 0 
        packet[90] = forecast_rule 
        struct.pack_into('<H', packet, 91, sunrise_davis)
        struct.pack_into('<H', packet, 93, sunset_davis)
        
        crc = crc16(packet[0:97])
        struct.pack_into('>H', packet, 97, crc)
        return bytes(packet)
