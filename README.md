**WeatherLinkIP Emulator for WeeWX**

A WeeWX extension that emulates a Davis WeatherLinkIP (WLIP) data logger over TCP/IP. This allows third‑party weather software (such as WeatherCat, CumulusMX, ~~WeatherLink PC~~, MeteoBridge, Home Assistant, etc.) to connect directly to your WeeWX instance. 

A WeeWX extension that emulates a Davis WeatherLinkIP (WLIP) data logger over TCP/IP. This allows you to connect third-party weather software (such as WeatherCat, CumulusMX, WeatherLink PC, MeteoBridge, Home Assistant, etc.) directly to your WeeWX instance.
The emulator acts as a bridge: it reads weather data from your WeeWX database (regardless of your actual station hardware) and serves it to client software using the proprietary Davis protocol.

⸻

**✨ Features (Version 83)**

⚡ **Interruptible LOOP (Fixes b'LO' Error)**

New in V83: The emulator now uses smart socket monitoring (select). It instantly stops streaming live data if a client sends a command (such as a wake‑up signal).

Benefit: Resolves the common “Bad wake‑up response: b’LO’” error found in previous versions, ensuring rock‑solid stability with WeeWX and clients that frequently switch between Live and Command modes.

⸻

🚦 **VIP Port Mapping (Multi‑Client Isolation)**

New in V80–V83: Open multiple listening ports simultaneously.

Why? Isolate critical production software (for example, a secondary WeeWX instance) on a dedicated VIP lane (e.g., port 22223) while keeping noisy devices (like Home Assistant) on the default port (22222). This prevents one client from blocking another.

⸻

💾 **Hardware‑Accurate History (DMPAFT)**

Improved in V83: The emulator mimics the physical memory limits of a real Davis data logger (≈ 2560 records).

Safety behavior:
	•	If a client requests all history since timestamp 0, the emulator calculates the correct start time based on the archive interval.
	•	Only the most recent full buffer of data is returned.

This prevents massive database dumps that could hang or destabilize connections.

⸻

🚀 **TCP Ring Buffer**

New in V78: Completely rewritten TCP reception logic using a ring buffer.

Result: Commands are never dropped, even if they arrive fragmented or if multiple commands arrive in a single network packet.

⸻

📦 **Installation**

This extension follows the standard WeeWX extension installer format. It automatically copies files and updates weewx.conf.

**Method 1: Install directly from GitHub (Recommended)**

Run the following command on your WeeWX server.

WeeWX v5 and newer

weectl extension install https://github.com/iiseppi/wlip-emulator/archive/main.zip

WeeWX v4

wee_extension --install https://github.com/iiseppi/wlip-emulator/archive/main.zip


⸻

Method 2: Install manually
	1.	Download wlip_emulator.py from the repository.
	2.	Copy it to your WeeWX user directory (commonly one of the following):
	•	/usr/share/weewx/bin/user/
	•	/home/weewx/bin/user/
	3.	Edit weewx.conf and add the configuration options shown below.
	4.	Add user.wlip_emulator.WeatherLinkEmulator to the user_services list.

⚙️ **Configuration**

The installer automatically adds the required sections to /etc/weewx/weewx.conf. You may verify or customize them as needed.

[WeatherLinkEmulator]
    # 1. Default Port (Standard Davis port is 22222)
    # Open to all connections not listed in client_mapping.
    port = 22222
    
    # 2. VIP Port Mapping (Optional but Recommended)
    # Assign specific IPs to dedicated ports to prevent conflicts.
    # Format: IP_ADDRESS:PORT, IP_ADDRESS:PORT
    # Example: Map local WeeWX to 22223, another client to 22224
    client_mapping = 192.168.1.50:22223, 192.168.1.51:22224

    # General settings
    max_clients = 10
    
    # (Optional) Force a specific archive interval in minutes.
    # Usually auto-detected from [StdArchive].
    # archive_interval = 1

⸻

Enable the Service

Ensure the emulator is listed under Services in the [Engine] section:




[Engine]
    [[Services]]
	# ... other services ...
	user_services = user.wlip_emulator.WeatherLinkEmulator


⸻

⚠️ **Important Note on Archive Intervals**

For history downloads to work correctly, the WeeWX archive interval must match the client’s expectations.
	•	WeeWX setting: In [StdArchive], the interval is defined in seconds (e.g., 60 or 300).
	•	Emulator behavior: Automatically reads this value and converts it to minutes (e.g., 1 or 5) for the Davis protocol.
	•	Client setting: Ensure your client software (WeatherCat, CumulusMX, etc.) is configured for the same interval.

⸻

📜 **Changelog**

V83: Updated DMPAFT logic to emulate real Davis Logger memory limits (Hardware Record Limit).

V83: Added 'select' to handle_loop to detect client interrupts (fixes b'LO' error).

V82: Added logging for connection target port info.

V81: Logic update to ensure Default Port remains open alongside VIP Ports.

V80: Added dynamic IP-to-Port mapping via config file.

V79: Added initial Dual Port support.

V78: Implemented Ring Buffer to fix command dropping (CRITICAL FIX).

V77: Fixed sticky packet handling with byte-by-byte check.

V74: Added active connection logging.

V65: Implemented Smart Identity (IP Filtering) for Legacy PC support.

V63: Added NACK response for Mystery Command (0x12 0x4d).

V57-V58: Implemented aggressive wake-up timing for sluggish consoles.

V50: Added shutDown() to prevent "Address already in use" on restarts.

V50: Added stale data check (>120s). Stops sending LOOP data if WeeWX/Station is down.

V50: HISTORY catch-up logic extended to 30 days & buffer increased to 50,000 records.

V50: FIXED Archive Interval reporting at EEPROM address 0x2D.



⸻

📄 License

This project is open source, licensed under the GNU General Public License v3.0 (GPLv3).

Based on contributions from the WeeWX community.
