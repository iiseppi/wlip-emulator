# installer derived from the weewx Belchertown skin installer
# https://raw.githubusercontent.com/poblabs/weewx-belchertown/master/install.py
# which was Copyright Pat O'Brien, with re-fomatting from a PR by Vince Skahan 

import configobj
from setup import ExtensionInstaller

# Python 3
from io import StringIO

#-------- extension info -----------

VERSION      = "0.6"
NAME         = 'wlip-emulator'
DESCRIPTION  = 'WeatherLinkIP Emulator'
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

    binding = wx_binding
"""
config_dict = configobj.ConfigObj(StringIO(extension_config))

#----------------------------------
#  files and services stanzas
#----------------------------------
files=[('bin/user', ['wlip_emulator.py'])]
files_dict = files

process_services = ['user.wlip_emulator.WeatherLinkEmulator']
process_dict = process_services

#---------------------------------
#          done
#---------------------------------
