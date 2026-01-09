
# installer derived from the weewx Belchertown skin installer
# https://raw.githubusercontent.com/poblabs/weewx-belchertown/master/install.py
# which was Copyright Pat O'Brien, with re-fomatting from a PR by Vince Skahan 

import configobj
from setup import ExtensionInstaller

# Python 3
from io import StringIO

#-------- extension info -----------

VERSION      = "0.5"
NAME         = 'wlip-emulator'
DESCRIPTION  = 'wlip emulator'
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
#         config stanza
#----------------------------------

extension_config = """

[WeatherLinkEmulator]
    # 1. Default Port
    # This port (22222) is open to all connections.
    # Should work with WeatherCat, Weather Display and CumulusMX simultaneusly.
    port = 22222

    # 2. Client Mapping (VIP Ports)
    # Format: IP_ADDRESS:PORT
    # This maps specific IPs to dedicated ports.
    # The line below opens port 22223 ONLY for 192.168.1.2 (Your WeeWX client).
    # All Weewx clients should be mapped here to their own VIP port.
    # Multiple clients can be mapped by adding additional lines.
    client_mapping = 192.168.1.2:22223

    # General settings
    max_clients = 10
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
