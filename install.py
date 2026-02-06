# installer derived from the weewx Belchertown skin installer
# https://raw.githubusercontent.com/poblabs/weewx-belchertown/master/install.py
# which was Copyright Pat O'Brien, with re-formatting from a PR by Vince Skahan

import configobj
from setup import ExtensionInstaller

# Python 3
from io import StringIO

#-------- extension info -----------
<<<<<<< development
VERSION      = "0.89"
NAME         = 'wlip-emulator'
DESCRIPTION  = 'Davis WeatherLink IP Emulator with LOOP/LOOP2 support'
=======

VERSION      = "0.6"
NAME         = 'wlip-emulator'
DESCRIPTION  = 'WeatherLinkIP Emulator'
>>>>>>> main
AUTHOR       = "iiseppi"
AUTHOR_EMAIL = "iiseppi@gmail.com"

#-------- main loader -----------
def loader():
    return WundergroundLikeInstaller()

class WundergroundLikeInstaller(ExtensionInstaller):
    def __init__(self):
        super(WundergroundLikeInstaller, self).__init__(
            version=VERSION,
            name=NAME,
            description=DESCRIPTION,
            author=AUTHOR,
            author_email=AUTHOR_EMAIL,
            config=config_dict,
            files=files_dict,
            process_services=process_dict
        )

#----------------------------------
#          config stanza
#----------------------------------
extension_config = """
[WeatherLinkEmulator]
<<<<<<< development
    # =============================================================================
    # WLIP EMULATOR V89 - Configuration
    # =============================================================================
    
    # -----------------------------------------------------------------------------
    # PORT CONFIGURATION
    # -----------------------------------------------------------------------------
    
    # 1. Default Port (open to all connections)
    # This port accepts connections from any IP address.
    # Multiple clients can connect simultaneously.
    # Recommended for: WeatherCat, Weather Display, CumulusMX
    port = 22222
    
    # 2. VIP Port Mapping (dedicated ports for specific IPs)
    # Format: IP_ADDRESS:PORT, IP_ADDRESS:PORT
    # Each VIP gets a dedicated port that ONLY that IP can connect to.
    # 
    # EXAMPLES:
    # Single VIP client:
    #   client_mapping = 192.168.1.50:22223
    #
    # Multiple VIP clients:
    #   client_mapping = 192.168.1.50:22223, 192.168.1.51:22224, 192.168.1.100:30000
    #
    # IMPORTANT: Assign a unique VIP port for each WeeWX instance that connects!
    # Example for WeeWX on another server:
    #   client_mapping = 192.168.1.100:22223
    #
    client_mapping = 
    
    # Maximum simultaneous clients per port
    max_clients = 10
    
    # -----------------------------------------------------------------------------
    # STATION CONFIGURATION
    # -----------------------------------------------------------------------------
    
    # Station type identifier
    # 16 = Vantage Pro 2 (default, most compatible)
    # 17 = Vantage Vue
    station_type = 16
    
    # Database binding (usually leave as default)
=======
    # 1. Default Port
    # This port (22222) is open to all connections.
    port = 22222

    # 2. General Settings
    max_clients = 10
    
    # 3. Logging Level
    # 0 = Basic (Only errors and connection info)
    # 1 = Stats (Shows Lag time, Temp, Wind) - RECOMMENDED
    # 2 = Raw (Hex dump of all traffic)
    debug_detail = 1

    # 4. Soft Start (Startup Delay)
    # Waiting time in seconds after WeeWX starts before opening the network port.
    # Allows USB drivers and console connection to stabilize.
    startup_delay = 15

    # 5. Watchdog (Freeze Protection)
    # How many seconds can data be stale before action is taken?
    # 300 = 5 minutes
    max_lag_threshold = 300

    # What action to take when threshold is exceeded?
    # 0 = Log Warning only
    # 1 = Disconnect Client (Forces reconnect)
    # 2 = Kill WeeWX Process (Forces systemd to restart WeeWX and reset USB)
    max_lag_action = 2

    # 6. Client Mapping (VIP Ports) - Optional
    # Format: IP_ADDRESS:PORT
    # client_mapping = 192.168.X.X:22223

>>>>>>> main
    binding = wx_binding
    
    # Archive interval override (optional)
    # If not set, will use StdArchive/archive_interval from weewx.conf
    # Value is in MINUTES (not seconds)
    # archive_interval = 5
    
    # -----------------------------------------------------------------------------
    # LOGGING & DEBUGGING
    # -----------------------------------------------------------------------------
    
    # Debug detail level
    # 0 = Basic (connections/disconnections only)
    # 1 = Statistics (includes data lag, packet info, forecast details)
    # 2 = Raw Hex (shows all incoming/outgoing bytes - VERY VERBOSE)
    debug_detail = 0
    
    # -----------------------------------------------------------------------------
    # STARTUP & RELIABILITY
    # -----------------------------------------------------------------------------
    
    # Soft Start Delay (seconds)
    # Wait this many seconds before opening ports after WeeWX starts.
    # Useful if USB drivers or network take time to initialize.
    # Set to 0 to disable (default).
    startup_delay = 0
    
    # -----------------------------------------------------------------------------
    # WATCHDOG SETTINGS (Data Staleness Detection)
    # -----------------------------------------------------------------------------
    
    # Maximum data lag threshold (seconds)
    # If WeeWX data becomes stale (no updates for this many seconds),
    # the watchdog will trigger an action.
    # Set to 0 to disable watchdog (default).
    # Recommended: 120 (2 minutes) for production use
    max_lag_threshold = 0
    
    # Watchdog action when threshold is exceeded
    # 0 = Log only (write warning to log, continue serving stale data)
    # 1 = Disconnect client (close connection, client must reconnect)
    # 2 = Kill WeeWX (force systemd restart - NUCLEAR OPTION)
    #
    # IMPORTANT: Option 2 requires systemd with Restart=always
    # Only use if you understand the implications!
    max_lag_action = 0
    
    # =============================================================================
    # CONFIGURATION EXAMPLES
    # =============================================================================
    
    # Example 1: Basic setup (single PC running Weather Display)
    # port = 22222
    # station_type = 16
    # debug_detail = 0
    
    # Example 2: Multiple weather software on different PCs
    # port = 22222
    # client_mapping = 192.168.1.50:22223, 192.168.1.51:22224
    # station_type = 16
    # debug_detail = 1
    
    # Example 3: Production server with watchdog
    # port = 22222
    # station_type = 16
    # debug_detail = 1
    # startup_delay = 10
    # max_lag_threshold = 120
    # max_lag_action = 1
    
    # Example 4: Mission-critical with automatic restart
    # port = 22222
    # station_type = 16
    # debug_detail = 1
    # startup_delay = 15
    # max_lag_threshold = 180
    # max_lag_action = 2
    # NOTE: Requires systemd service with Restart=always
    
    # =============================================================================
    # TROUBLESHOOTING
    # =============================================================================
    
    # Problem: Clients can't connect
    # Solution: Check firewall rules (sudo ufw allow 22222/tcp)
    #           Verify port not already in use (sudo netstat -tulpn | grep 22222)
    #           Check debug_detail = 1 for connection logs
    
    # Problem: Data appears stale or delayed
    # Solution: Enable watchdog (max_lag_threshold = 120, max_lag_action = 1)
    #           Check WeeWX loop interval in weewx.conf
    #           Set debug_detail = 1 to see data lag info
    
    # Problem: Multiple clients interfering with each other
    # Solution: Use VIP port mapping (client_mapping)
    #           Assign each client a dedicated port
    
    # Problem: Extension crashes on startup
    # Solution: Enable startup_delay = 10
    #           Check WeeWX log for errors
    #           Verify database is accessible
    
    # Problem: Want to see forecast values
    # Solution: Set debug_detail = 1
    #           Check log for "Forecast: Rule=X Baro=Y"
    #           Verify barometer and barometerTrend are in LOOP packets
"""

config_dict = configobj.ConfigObj(StringIO(extension_config))

#----------------------------------
#  files and services stanzas
#----------------------------------

files = [('bin/user', ['bin/user/wlip_emulator.py'])]
files_dict = files

process_services = ['user.wlip_emulator.WeatherLinkEmulator']
process_dict = process_services

#---------------------------------
#          done
#---------------------------------
