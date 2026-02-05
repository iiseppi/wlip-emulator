# WeatherLinkIP Emulator for WeeWX

A WeeWX extension that emulates a Davis WeatherLinkIP (WLIP) data logger over TCP/IP. This allows third‑party weather software (such as WeatherCat, CumulusMX, MeteoBridge, Home Assistant, etc.) to connect directly to your WeeWX instance.

The emulator acts as a bridge: it reads weather data from your WeeWX database (regardless of your actual station hardware) and serves it to client software using the proprietary Davis protocol.

---

## ✨ Key Features (Version 88)

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
2. Copy it to your WeeWX user directory (e.g., `/usr/share/weewx/bin/user/`).
3. Add `user.wlip_emulator.WeatherLinkEmulator` to the `user_services` list in `weewx.conf`.
4. Add the configuration block below.

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

---

## 📜 Changelog

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
