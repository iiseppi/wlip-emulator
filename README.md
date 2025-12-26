
# wlip-emulator: The Modern VirtualVP for WeeWX

**A WeeWX service that emulates a Davis WeatherLink IP (WLIP) data logger over the network.**

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

## 📝 Introduction

**Are you looking for a modern replacement for VirtualVP?**

This plugin allows your **WeeWX** station to act as a **Davis Vantage Pro 2** weather station equipped with a WeatherLink IP interface. It opens a TCP socket (default port 22222) and speaks the native Davis Serial Communication Protocol.

This enables you to connect 3rd party weather software (such as **CumulusMX**, **Weather Display**, **WeatherCat**, etc.) directly to your WeeWX instance via the local network simultaneously.

Essentially, it turns WeeWX into a "software splitter" or a "virtual console", feeding data to multiple clients regardless of your actual weather station hardware.

## ✨ Key Features (Updated V43)

* **Broad Compatibility:** Now fully compatible with **CumulusMX** and **Weather Display**, in addition to the original **WeatherCat** support.
* **VirtualVP Replacement:** Acts as a modern, open-source alternative to the abandoned VirtualVP software. No virtual serial ports required—everything runs over standard TCP/IP.
* **Atomic Sends (New in V43):** Implements "Atomic Send" logic to merge ACK and Data packets. This eliminates "Expected data not available" errors and timeouts in strict clients like CumulusMX.
* **Loop 2 Support:** Intelligently handles `LPS 2 1` requests. It ensures stability by serving standard Loop 1 data to clients that might otherwise crash on emulated Loop 2 packets.
* **Live Data Emulation:** Sends `LOOP` packets in real-time to connected clients.
* **History/Archive Download:** Supports `DMP` and `DMPAFT` commands. Allows client software to "catch up" on missing data from the WeeWX database after downtime.
* **Smart Data Handling:**
    * Converts WeeWX units to Davis US/Imperial units automatically.
    * Handles "Dash Values" (e.g., `0xFF`, `32767`) correctly to prevent bad data points.
    * Supports Solar Radiation and UV index.

## ✅ Tested Software

| Software | Status | Notes |
| :--- | :--- | :--- |
| **WeatherCat** (macOS) | ✅ Working | Original development platform. |
| **CumulusMX** | ✅ Working | Requires V43+ (Fixed timeouts & interval config loops). |
| **Weather Display** | ✅ Working | Requires V43+ (Fixed timing sensitivity). |
| **WeatherLink PC** | ❔ Untested | Likely works, feedback needed. |

## 🚀 Installation

### Prerequisites
* WeeWX installed and running (Version 4.x or 5.x).
* Python 3.

### Step 1: Install the Script
1.  Download `wlip_emulator.py` (ensure you have the latest V43 or newer).
2.  Place the file in your WeeWX user directory:
    * **Debian/RPM/Package installs:** `/etc/weewx/bin/user/`
    * **Pip installs:** `~/weewx-data/bin/user/`

### Step 2: Configure weewx.conf
Open your `weewx.conf` file and make the following changes.

**A. Add the configuration block:**
Add this section to the bottom of the file (or anywhere at the root level):

```ini
[WeatherLinkEmulator]
    # The port to listen on (Davis default is 22222)
    port = 22222
    
    # Maximum concurrent clients
    max_clients = 5
    
    # Optional: Force the reported archive interval in minutes.
    # IMPORTANT: If your client software (e.g. WeatherCat) expects 1 minute
    # but WeeWX is set to 5 minutes, force this to 1.
    archive_interval = 1


B. Enable the Service:
Find the [Engine] section and look for [[Services]]. Add user.wlip_emulator.WeatherLinkEmulator to the process_services list. It should look something like this:

Ini, TOML


[Engine]
    [[Services]]
        # ... other services ...
        process_services = weewx.engine.StdConvert, weewx.engine.StdCalibrate, weewx.engine.StdQC, weewx.wxservices.StdWXCalculate, user.wlip_emulator.WeatherLinkEmulator, weewx.engine.StdArchive


Step 3: Restart WeeWX
Restart the WeeWX service to load the new emulator:

Bash


sudo systemctl restart weewx


Check the logs to ensure it started correctly:

Bash


sudo journalctl -u weewx -f


Look for a line similar to: INFO user.wlip_emulator: *** WLIP EMULATOR V43 ... STARTED ***
🔌 Connecting Client Software
Generic Instructions:
Open your client software (CumulusMX, WeatherCat, etc.).
Go to the station setup/configuration.
Select Davis Vantage Pro 2 (or generic Davis) as the station type.
Select Network / TCP/IP (WeatherLinkIP) as the connection method.
Enter the IP address of your WeeWX server.
Enter the Port (default 22222).
Connect.
The software should detect the station, download any missing history (archive records), and then start displaying live data.
🧪 Call for Testers: Database Formats
The emulator reads data from your WeeWX database and converts it back to the raw Davis binary format. To ensure this conversion is accurate for everyone, I need feedback from users running different database schemas in weewx.conf:
US (Imperial)
Metric
MetricWX
Please let me know via GitHub Issues if the values (Temperature, Pressure, Wind, etc.) appearing in your client software match your WeeWX data correctly.
🛠 Troubleshooting
"Expected data not available" / Disconnects in CumulusMX:
Ensure you are running V43 or later. Older versions had timing issues with ACK packets that caused strict clients like CumulusMX to drop the connection.
Client connects but shows no data / "Download History" hangs:
Check the archive_interval setting in weewx.conf. If your WeeWX database saves every 5 minutes (300 seconds), but your client software is set to 1 minute, the history calculation will fail.
Fix: Set archive_interval = 1 in the [WeatherLinkEmulator] section of weewx.conf.
History download fails (0 records found):
The emulator attempts to read the database using the default WeeWX database binding. If you use a custom database setup (e.g., MySQL with a non-standard binding name), ensure the default binding points to valid data.
"Connection Refused":
Check that port 22222 is open on your firewall (e.g., sudo ufw allow 22222).
Ensure WeeWX is actually running.
📄 License
This project is licensed under the GNU General Public License v3.0 - see the LICENSE file for details.
This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
