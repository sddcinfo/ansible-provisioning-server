"""smcbmc - Supermicro BMC Management CLI"""

__version__ = "1.0.0"

# Redfish API path constants
REDFISH_BASE = "/redfish/v1"
REDFISH_SYSTEMS = f"{REDFISH_BASE}/Systems/1"
REDFISH_CHASSIS = f"{REDFISH_BASE}/Chassis/1"
REDFISH_MANAGERS = f"{REDFISH_BASE}/Managers/1"

REDFISH_RESET_ACTION = f"{REDFISH_SYSTEMS}/Actions/ComputerSystem.Reset"
REDFISH_THERMAL = f"{REDFISH_CHASSIS}/Thermal"
REDFISH_POWER = f"{REDFISH_CHASSIS}/Power"
REDFISH_STORAGE = f"{REDFISH_SYSTEMS}/Storage"
REDFISH_MEMORY = f"{REDFISH_SYSTEMS}/Memory"
REDFISH_NETWORK_INTERFACES = f"{REDFISH_SYSTEMS}/NetworkInterfaces"
REDFISH_ETHERNET_INTERFACES = f"{REDFISH_SYSTEMS}/EthernetInterfaces"
REDFISH_MANAGER_ETHERNET = f"{REDFISH_MANAGERS}/EthernetInterfaces"
REDFISH_MANAGER_NETWORK_PROTOCOL = f"{REDFISH_MANAGERS}/NetworkProtocol"
REDFISH_VIRTUAL_MEDIA = f"{REDFISH_MANAGERS}/VirtualMedia"
REDFISH_UPDATE_SERVICE = f"{REDFISH_BASE}/UpdateService"
REDFISH_FIRMWARE_INVENTORY = f"{REDFISH_UPDATE_SERVICE}/FirmwareInventory"

# CGI paths for screenshot/console
CGI_LOGIN = "/cgi/login.cgi"
CGI_LOGOUT = "/cgi/logout.cgi"
CGI_CAPTURE_PREVIEW = "/cgi/CapturePreview.cgi"

# Default retry/backoff settings
DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT = 30

# Boot device targets
BOOT_DEVICES = ["None", "Pxe", "Hdd", "Cd", "BiosSetup", "UefiShell", "Usb"]
