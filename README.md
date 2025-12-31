# WeatherLinkIP Emulator for WeeWX

A WeeWX extension that emulates a **Davis WeatherLinkIP (WLIP)** data logger over TCP/IP. This allows you to connect third-party weather software (such as **WeatherCat**, **CumulusMX**, **WeatherLink PC**, **MeteoBridge**, etc.) directly to your WeeWX instance.

The emulator acts as a bridge: it reads weather data from your WeeWX database (regardless of your actual station hardware) and serves it to client software using the proprietary Davis protocol.

### ✨ Features (Version 49)

* **✅ Full Protocol Emulation:** Emulates the Davis TCP/IP protocol on port 22222.
* **🧠 Smart History Catch-up (30 Days):** If a client requests "all history" (timestamp 0)—common during fresh installs—the emulator automatically limits the response to the last **30 days**. This prevents massive data downloads that can hang older software, while ensuring a full month of recent history is available immediately.
* **🔧 EEPROM Interval Fix:** Correctly reports the `Archive Interval` at memory address `0x2D`. This is critical for **WeatherCat** and other strict clients to calculate history requirements correctly without errors.
* **🚀 High-Capacity Buffer:** Support for up to 50,000 records in a single batch, allowing robust data transfer even with dense 1-minute logging intervals.
* **⚡ Atomic Sends:** Improved TCP packet handling for better stability on unstable networks.

---

## Installation

This extension follows the standard WeeWX extension installer format. It will automatically copy files and update your `weewx.conf`.

### Method 1: Install directly from GitHub (Recommended)

Run the following command on your WeeWX server.

** For WeeWX v5 and newer **

weectl extension install https://github.com/iiseppi/wlip-emulator/archive/main.zip

** For WeeWX v4 **

wee_extension --install https://github.com/iiseppi/wlip-emulator/archive/main.zip

### Method 2: Install manually

Download wlip_emulator.py from this repository.

Copy the file to your WeeWX user directory (usually /usr/share/weewx/user/ or /home/weewx/bin/user/).

Edit your weewx.conf and add the configuration options listed below.

Add user.wlip_emulator.WeatherLinkEmulator to the user_services list in weewx.conf.


### Configuration

The installer will automatically add the necessary sections to your /etc/weewx/weewx.conf. You can verify or modify them:

[WeatherLinkEmulator]
    # TCP port to listen on (Davis default is 22222)
    port = 22222
    
    # Maximum number of simultaneous clients
    max_clients = 5
    
    # (Optional) Force a specific archive interval in minutes (1, 5, 10, 15, 30, 60, 120).
    # If not set, the emulator detects the interval from WeeWX [StdArchive] settings.
    # IMPORTANT: Only set this if you have a specific reason. The emulator 
    # automatically adapts to 1-min or 5-min intervals based on your database.
    # archive_interval = 1

Enable the Service: Ensure the emulator is listed in the [Engine] section under Services:

[Engine]
    [[Services]]
        # ... other services ...
        user_services = user.wlip_emulator.WeatherLinkEmulator

⚠️ ### Important Note on Archive Intervals ###

For history downloads to work correctly, the WeeWX Archive Interval must match what the client expects.

WeeWX Setting: In [StdArchive], the interval is set in seconds (e.g., 60 or 300).

Emulator Behavior: The emulator automatically reads this value and converts it to minutes (e.g., 1 or 5) for the Davis protocol.

Client Setting: Ensure your client software (WeatherCat, etc.) is expecting the same interval.

Note: With Version 49, WeatherCat should automatically detect the correct interval from the emulated EEPROM.

License
This project is open-source, licensed under the GNU General Public License v3.0 (GPLv3). Based on contributions from the WeeWX community.