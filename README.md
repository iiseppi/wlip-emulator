# WeatherLinkIP Emulator for WeeWX

A WeeWX extension that emulates a Davis WeatherLinkIP (WLIP) data logger over TCP/IP. This allows weather software (such as WeatherLink PC, WeatherCat, CumulusMX, MeteoBridge, Home Assistant, etc.) to connect directly to your WeeWX instance.

The emulator acts as a bridge: it reads weather data from your WeeWX database (regardless of your actual station hardware) and serves it to client software using the proprietary Davis protocol.

---

## ✨ Key Features (Version 93)

### 🔍 Checked Against a Real Console

**New in V93:** a serial capture of WeatherLink PC talking to a Vantage Pro2 console (thanks to the reporter of issue #8) was compared command by command with what the emulator answers. Two differences were fixed:

* **A WeatherLink station with a record dated in the future** asks for data after that date on every download (2125 in the capture). A console cannot find such a time stamp and, as the Davis protocol says, sends its whole memory. The emulator now does the same and sends the newest 2560 records; before V93 the station got no records at all. To get rid of the bogus record, delete it in WeatherLink (**Browse → Choose Date**, last date, **Delete**).
* **Station list in the EEPROM** (built from `[Station]`) now has the same bytes as a console for the ISS on transmitter 1.

### 📚 Long Downloads and the Whole History

**New in V92:**

* **"Function to retrieve data through TCP/IP protocol from the device timed out":** WeatherLink PC waits only about 2 seconds for the `DMPAFT` page header. V91 read every record before sending it, which took too long when WeatherLink's newest record was old (up to 50,000 records). V92 counts the records with one quick query, sends the header at once and reads the records 100 at a time as the pages are asked for. The long read in V91 also held a database read lock long enough for WeeWX's own archive write to fail with "database is locked", and `weewxd` then restarted its engine, dropping every client.
* **Time to confirm the download:** WeatherLink PC asks "Transfer N pages to your PC?" after the header and asks for the first page only when you click OK. The emulator now waits up to 5 minutes for that.
* **The whole database to WeatherLink:** with `new_station_history = all`, a new WeatherLink station (one with no data of its own) gets the WeeWX database from the oldest record onwards, `max_dmpaft_records` per download. See *History older than WeatherLink's own data* under **Using WeatherLink PC** below.
* `max_dmpaft_records` is limited to 327,675 records (65,535 pages), the most the 16-bit page count can hold.

### 🖥️ WeatherLink PC Support

**New in V91:** Davis' own WeatherLink PC software can connect, identify the station as a Vantage Pro2 and download the archive. Earlier versions failed in three ways, all visible in a Wireshark capture of a WeatherLink PC session:

* **"Vantage Vue" instead of Vantage Pro2:** the station type command (`WRD`) answered 17, which is the Vue code. It now answers 16 (Vantage Pro/Pro2), configurable with `station_type`.
* **Endless download loop:** WeatherLink PC sends the 6-byte `DMPAFT` time stamp as two TCP segments (1 + 5 bytes). The old code read it with a single `recv(6)`, got one byte and gave up silently, so WeatherLink PC retried forever. Every connection now has one buffered reader, and binary payloads are read with exact lengths, timeouts and CRC checks.
* **Broken wake-up after `WRD`:** WeatherLink PC sends `WRD` and its argument bytes separately; the leftover bytes were later answered with a NAK in place of the wake-up reply.

V91 also adds:

* **Real console settings:** when WeeWX drives a Vantage console, the emulator reads its EEPROM (units, rain collector, location, transmitters, calibration) at start-up and serves that. Other stations get an EEPROM image built from `[Station]` in `weewx.conf`.
* **Correct rain collector:** rain is sent in the bucket clicks the EEPROM says (0.01 in, 0.2 mm or 0.1 mm). Before V91 it was always sent as 0.01 in clicks, about 21 % off for 0.2 mm collectors.
* **Spec-accurate LOOP and LOOP2 packets:** dash values for missing sensors, north wind sent as 360°, barometer trend, sunrise/sunset, `LPS` packet masks.
* **HILOWS from the database:** day, month and year highs and lows come from the WeeWX daily summaries instead of zeros.
* **More commands:** `SETTIME` (acknowledged, the WeeWX host keeps the time), `BARDATA`, `RXCHECK`, `RXTEST`, `RECEIVERS`, `EEBWR`/`EEWR` (kept in memory only), `DMP`, `SETPER`, `NEWSETUP`, `START`/`STOP`, `LAMPS`, `CLR*` (acknowledged, nothing is cleared).
* **Exact command matching:** `RXTEST` is no longer answered as `TEST`, and `SETTIME` is no longer ignored.

### 🔌 Graceful Shutdown

**V89:** Fully modernized for WeeWX 5.5+ standards. Uses the new `weewx.SHUTDOWN` event to close network sockets gracefully. This eliminates the infamous "Address already in use" port errors when restarting WeeWX. Includes TCP Keepalive to instantly detect and drop dead client connections.

### 🛡️ Robust Watchdog (Self-Healing)

**New in V87-V88:** The emulator includes a configurable "Dead Man's Switch" to handle hardware freezes (e.g., USB connection drops common with Vantage consoles).

* **Detection:** Monitors the lag between the current time and the latest weather packet.
* **Action:** If data becomes stale (e.g., >5 minutes), it can automatically kill the WeeWX process.
* **Result:** This forces `systemd` to restart the service, resetting USB ports and drivers without user intervention.

### ⏳ Soft Start (Startup Delay)

**New in V85:** Configurable delay before opening network ports.

* **Benefit:** Gives the host machine time to initialize USB drivers and establish a stable connection to the weather station console before accepting client connections. Prevents race conditions during boot.

### 📊 Smart Logging

**New in V84:** Adjustable logging levels via `weewx.conf`.

* **Level 0 (Basic):** Minimal logs, errors only.
* **Level 1 (Stats):** Shows Lag time, Temperature, and Wind speed for every packet batch. Perfect for diagnostics.
* **Level 2 (Raw):** Full Hex dump of TCP traffic for deep debugging.

### ⚡ Interruptible LOOP (Fixes b'LO' Error)

**V83:** Uses smart socket monitoring (`select`) to instantly stop streaming live data if a client sends a command. Resolves the "Bad wake‑up response: b’LO’" error, ensuring stability with clients that frequently switch between Live and Command modes.

### 🚦 VIP Port Mapping (Multi‑Client Isolation)

**V80–V83:** Open multiple listening ports simultaneously. Isolate critical production software on a dedicated VIP lane (e.g., port 22223) while keeping noisy devices (like Home Assistant) on the default port (22222).

### 💾 Hardware‑Accurate History (DMPAFT)

**V83:** Mimics physical memory limits (≈ 2560 records) and prevents 0x2D EEPROM errors by clamping archive intervals to 255 minutes. Prevents massive database dumps that could hang connections.

### 💡 Pro-tip: Extended Historical Recovery
If a specific start date is explicitly requested by the client software (e.g., by modifying today.ini in CumulusMX), the emulator bypasses the 2560 record hardware limit and allows a massive historical catch-up (up to 50,000 records). Use with caution: Downloading weeks of data at once works perfectly, but may take several minutes to process depending on your client software.

---

## 📦 Installation

**Method 1: Install directly from GitHub (Recommended)**

Run the following command on your WeeWX server.

**WeeWX v5 and newer:**

```bash
weectl extension install https://github.com/iiseppi/wlip-emulator/archive/main.zip

```

**WeeWX v4:**

```bash
wee_extension --install https://github.com/iiseppi/wlip-emulator/archive/main.zip

```

**Method 2: Install manually**

1. Download `wlip_emulator.py` from the repository.
2. Copy it to your WeeWX user directory (e.g., `/etc/weewx/bin/user/` on WeeWX 5 packages, `/usr/share/weewx/user/` on WeeWX 4).
3. Add `user.wlip_emulator.WeatherLinkEmulator` to the end of the `process_services` list in `weewx.conf`.
4. Add the configuration block below.

**Testing a branch before it is merged:** replace `main` in the URL with the branch name, for example `.../archive/weatherlink-pc.zip`.

---

## ⚙️ Configuration

The installer automatically adds the required sections to `/etc/weewx/weewx.conf`.

```ini
[WeatherLinkEmulator]
    # 1. Default Port (Standard Davis port is 22222)
    port = 22222
    
    # 2. Logging Level
    # 0 = Basic (Errors only)
    # 1 = Stats (Shows Lag/Temp/Wind) - RECOMMENDED
    # 2 = Raw (Hex dump)
    debug_detail = 1

    # 3. Soft Start
    # Seconds to wait before opening ports. Allows USB drivers to settle.
    startup_delay = 15

    # 4. Watchdog (Freeze Protection)
    # Threshold: How many seconds can data be stale before action is taken?
    # 300 = 5 minutes
    max_lag_threshold = 300

    # Action: What to do when threshold is exceeded?
    # 0 = Log Warning only
    # 1 = Disconnect Client
    # 2 = Kill WeeWX Process (Forces systemd restart & USB reset) - RECOMMENDED
    max_lag_action = 2

    # 5. VIP Port Mapping (Optional)
    # Assign specific IPs to dedicated ports.
    # client_mapping = 192.168.1.50:22223

    max_clients = 10
    binding = wx_binding

```

All of the following are optional (new in V91, `new_station_history` in V92). The defaults suit WeatherLink PC and a Vantage Pro2.

```ini
    # Answer to the station type query (WRD): 16 = Vantage Pro/Pro2, 17 = Vantage Vue
    station_type = 16

    # Where the EEPROM image comes from:
    # auto    = read it from the Vantage console if WeeWX drives one, otherwise build it from [Station]
    # console = like auto, but log an error if there is no Vantage console
    # station = always build it from [Station] and [StdConvert]
    eeprom_source = auto

    # Rain collector: auto (from the EEPROM), 0 = 0.01 in, 1 = 0.2 mm, 2 = 0.1 mm
    rain_bucket = auto

    # Firmware answers to VER and NVER
    firmware_date = May 1 2012
    firmware_version = 1.90

    # Seconds between LOOP packets, and how long to wait for a client in the middle of
    # a binary exchange (DMPAFT time stamp, page ACKs, SETTIME)
    loop_interval = 2.0
    protocol_timeout = 10

    # Most records sent in one download when the client asks for data after a specific
    # time (at most 327675, which is 65535 pages)
    max_dmpaft_records = 50000

    # What a client with no data of its own (for example a new WeatherLink station) gets:
    # recent = the newest 2560 records, like a real console
    # all    = the whole database from the oldest record, max_dmpaft_records per download
    new_station_history = recent

    # Archive interval in minutes sent in EEPROM 0x2D. Normally taken from the database.
    # archive_interval = 5
```

---

## 🖥️ Using WeatherLink PC

1. In WeatherLink, open **Setup → Walkthrough** (or **Setup → Communications Port**) and choose **TCP/IP**. Enter the WeeWX server's IP address and the emulator's port (22222 by default).
2. Choose **Vantage Pro2** as the station type.
3. Use **Download** as usual. The first download without earlier data gets the last 2560 records, like a real console; later downloads get everything after WeatherLink's newest record, at most `max_dmpaft_records` (50,000) per download, oldest first.

If WeatherLink's database ends long ago (it asks for everything after its newest record), each download continues from there, so catching up to today can take several downloads. To get more per download, raise `max_dmpaft_records` (one download holds at most 327,675 records). Downloads of 10,000 pages (50,000 records) have been seen to work with WeatherLink 6.0.5; larger ones have not been tried yet. To skip the old period, start a new station in WeatherLink: with no earlier data it gets the newest 2560 records.

**History older than WeatherLink's own data.** WeatherLink only asks for records after its newest one, so it never fetches anything older by itself. To load the whole WeeWX database:

1. Set `new_station_history = all` (and, if you like, a larger `max_dmpaft_records`) and restart WeeWX.
2. In WeatherLink, create a new station with the same settings (**File → New Station**, then the walkthrough). With no data it gets the database from the oldest record onwards; repeat **Download** until it has caught up.
3. Either use the new station from now on, or close WeatherLink and copy its monthly `.wlk` files for the missing months into the old station's folder (keep a backup of the folder first).
4. Set `new_station_history` back to `recent` if other clients should behave like a real console.

Notes:

* Commands that would change the console (set time, clear archive, set archive interval, EEPROM writes) are acknowledged so WeatherLink does not stall, but nothing changes on the WeeWX side. EEPROM writes are kept in memory until WeeWX restarts.
* If something does not work, set `debug_detail = 2` and `debug = 1` in `weewx.conf`, restart WeeWX and collect the log, for example `sudo journalctl -u weewx | grep "WLIP Emulator"`. Unknown commands are logged at the info level as `Unsupported command from ...`.

---

## 🧪 Tests

The tests run the emulator against a temporary WeeWX database and talk to it over TCP, both with a replay of a WeatherLink PC session (including how it splits commands across TCP segments) and with the WeeWX Vantage driver in WeatherLinkIP mode. With a WeeWX source tree:

```bash
PYTHONPATH=/path/to/weewx/src python3 -m unittest discover -s tests -v
```

---

## 📜 Changelog

**V93:** `DMPAFT` with a time stamp after the newest record sends the newest 2560 records, like a console that cannot find the time stamp (a WeatherLink station with a record dated 2125 used to get nothing); ISS station list bytes match a console. Both found in the serial capture of issue #8.

**V92:** `DMPAFT` header sent at once (capped count query) and records read 100 at a time (fixes the WeatherLink PC time-out on long downloads and WeeWX restarts on "database is locked"); up to 5 minutes for WeatherLink's download confirmation; `max_dmpaft_records` limited to 65535 pages; `new_station_history = all` gives a new WeatherLink station the whole database.

**V91:** WeatherLink PC support: `WRD` answers Vantage Pro2 (16) and waits for its argument bytes; one buffered reader per connection with exact-length binary reads (fixes the `DMPAFT` loop); `DMPAFT` CRC check, page resend and cancel; `DMP`; EEPROM read from the Vantage console; rain clicks match the rain collector; spec-accurate LOOP/LOOP2; HILOWS from daily summaries; `SETTIME`, `BARDATA`, `RXCHECK` and other commands; exact command matching; database lock retries fixed (`weewx.CannotLock` does not exist); transmitted bytes logged at `debug_detail = 2`.

**V89:** Graceful shutdown via the `weewx.SHUTDOWN` event, SIGTERM watchdog restart, TCP keepalive.

**V88:** Fixed "Unable to read EEPROM at address 0x2D" error by clamping archive interval to hardware limits (max 255 mins).

**V87:** Added `max_lag_action` (Watchdog Kill Switch). Can kill WeeWX process to force systemd restart on freeze.

**V86:** Added `max_lag_disconnect`. Forces client disconnect if WeeWX data is stale.

**V85:** Added `startup_delay` (Soft Start) to allow USB drivers to settle.

**V84:** Implemented Smart Logging (`debug_detail` levels).

**V83:** Updated DMPAFT logic to emulate real Davis Logger memory limits; Added `select` to handle_loop to detect client interrupts.

**V82:** Added logging for connection target port info.

**V81:** Logic update to ensure Default Port remains open alongside VIP Ports.

**V80:** Added dynamic IP-to-Port mapping via config file.

**V78:** Implemented Ring Buffer to fix command dropping (CRITICAL FIX).

**V50:** Added stale data check (>120s) and HISTORY catch-up logic improvements.

---

## 📄 License

This project is open source, licensed under the GNU General Public License v3.0 (GPLv3). Based on contributions from the WeeWX community.
