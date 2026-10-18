"""Constants for the Sony Legacy API integration."""

from datetime import timedelta

DOMAIN = "sony"
DEFAULT_DEVICE_NAME = "Sony Device"
DEVICE_SCAN_INTERVAL = timedelta(seconds=60)

CONF_PIN = "pin"
CONF_APP_PORT = "app_port"
CONF_DMR_PORT = "dmr_port"
CONF_IRCC_PORT = "ircc_port"
CONF_MAC_ADDRESS = "mac_address"
CONF_COOKIES = "cookies"
CONF_UDN = "udn"
CONF_ENTITY_ID_HOST = "entity_id_host"
CONF_DMR_URL = "dmr_url"
CONF_IRCC_URL = "ircc_url"

SSDP_IRCC = "urn:schemas-sony-com:service:IRCC:1"
SSDP_MEDIA_RENDERER = "urn:schemas-upnp-org:device:MediaRenderer:1"

DEFAULT_APP_PORT = 50202
DEFAULT_DMR_PORT = 52323
DEFAULT_IRCC_PORT = 50001
