
# installer derived from the weewx Belchertown skin installer
# https://raw.githubusercontent.com/poblabs/weewx-belchertown/master/install.py
# which was Copyright Pat O'Brien, with re-fomatting from a PR by Vince Skahan 

import configobj
from setup import ExtensionInstaller

# Python 3
from io import StringIO

#-------- extension info -----------

VERSION      = "0.3.49"
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
    # The port to listen on (Davis default is 22222)
    port = 22222
    
    # Maximum concurrent clients
    max_clients = 5
    
    # Optional: Force the reported archive interval in minutes.
    # IMPORTANT: If your client software (e.g. WeatherCat) expects 1 minute
    # but WeeWX is set to 5 minutes, force this to 1. 
    # In many cases, you can leave this commented out to use WeeWX's archive interval.
    # archive_interval = 1

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
