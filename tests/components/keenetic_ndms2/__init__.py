"""Tests for the Keenetic NDMS2 component."""

from homeassistant.components.keenetic_ndms2 import const
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.helpers.service_info.ssdp import (
    ATTR_UPNP_FRIENDLY_NAME,
    ATTR_UPNP_UDN,
    SsdpServiceInfo,
)

MOCK_NAME = "Keenetic Ultra 2030"
MOCK_IP = "0.0.0.0"
SSDP_LOCATION = f"http://{MOCK_IP}/"

MOCK_DATA = {
    CONF_HOST: MOCK_IP,
    CONF_USERNAME: "user",
    CONF_PASSWORD: "pass",
    CONF_PORT: 23,
}

MOCK_RECONFIGURE = {
    CONF_USERNAME: "user1",
    CONF_PASSWORD: "pass1",
    CONF_PORT: 123,
}

MOCK_OPTIONS = {
    CONF_SCAN_INTERVAL: 15,
    const.CONF_CONSIDER_HOME: 150,
    const.CONF_TRY_HOTSPOT: False,
    const.CONF_INCLUDE_ARP: True,
    const.CONF_INCLUDE_ASSOCIATED: True,
    const.CONF_INTERFACES: ["Home", "VPS0"],
}

MOCK_SSDP_DISCOVERY_INFO = SsdpServiceInfo(
    ssdp_usn="mock_usn",
    ssdp_st="mock_st",
    ssdp_location=SSDP_LOCATION,
    upnp={
        ATTR_UPNP_UDN: "uuid:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
        ATTR_UPNP_FRIENDLY_NAME: MOCK_NAME,
    },
)
