# wlip-emulator
Emulating Davis WeatherLink I.P. with WeeWx for using multiple weather software with only one Davis Vantage Pro2 hardware


# WeeWX WeatherLink IP Emulator

**A WeeWX service that emulates a Davis WeatherLink IP (WLIP) data logger over the network.**

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

## 📝 Introduction

This plugin allows your **WeeWX** station to act as a **Davis Vantage Pro 2** weather station equipped with a WeatherLink IP interface. It opens a TCP socket (default port 22222) and speaks the native Davis Serial Communication Protocol.

This enables you to connect 3rd party weather software (such as **WeatherCat**, **WeatherLink PC**, **CumulusMX**, etc.) directly to your WeeWX instance via the local network, even if your actual weather station hardware is not a Davis station (e.g., Fine Offset, Ambient Weather, etc.).

Essentially, it turns WeeWX into a "virtual" Davis Vantage Pro 2 Console.

## ✨ Features

* **Live Data Emulation:** Sends `LOOP` packets in real-time to connected clients.
* **History/Archive Download:** Supports `DMP` and `DMPAFT` commands. This allows client software to "catch up" on missing data from the WeeWX database after being closed for a period of time.
* **EEPROM Emulation:** Responds to `EEBRD` and `EERD` commands to report console settings (like Archive Interval), ensuring clients calculate history requirements correctly.
* **Smart Data Handling:**
    * Converts WeeWX units to Davis US/Imperial units automatically.
    * Uses correct Davis "Dash Values" (e.g., `0xFF`, `32767`) for missing sensor data to prevent clients from rejecting records.
    * Supports Solar Radiation and UV index.
* **Multi-client:** Supports multiple simultaneous TCP connections.

## 🚀 Installation

### Prerequisites
* WeeWX installed and running (Version 4.x or 5.x).
* Python 3.

### Step 1: Install the Script
1.  Download `wlip_emulator.py`.
2.  Place the file in your WeeWX user directory.
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


Look for a line similar to: *** WLIP EMULATOR V20 STARTED ***
🔌 Connecting Client Software (e.g., WeatherCat)
Open your client software (WeatherCat, WeatherLink, etc.).
Go to the station setup.
Select Davis Vantage Pro 2 (or generic Davis) as the station type.
Select Network / TCP/IP (WeatherLinkIP) as the connection method.
Enter the IP address of your WeeWX server.
Enter the Port (default 22222).
Connect.
The software should detect the station, download any missing history (archive records), and then start displaying live data.
🛠 Troubleshooting
Client connects but shows no data / "Download History" hangs:
Check the archive_interval setting in weewx.conf. If your WeeWX database saves every 5 minutes (300 seconds), but your client software is set to 1 minute, the history calculation will fail.
Fix: Set archive_interval = 1 in the [WeatherLinkEmulator] section of weewx.conf.
History download fails (0 records found):
The emulator attempts to read the database using the default WeeWX database binding. If you use a custom database setup (e.g., MySQL with a non-standard binding name), ensure the default binding points to valid data.
"Connection Refused":
Check that the port 22222 is open on your firewall (e.g., sudo ufw allow 22222).
Ensure WeeWX is actually running.
📄 License
This project is licensed under the GNU General Public License v3.0 - see the LICENSE file for details.
This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
