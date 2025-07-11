"""Test the Philips TV config flow."""

from ipaddress import ip_address
from unittest.mock import ANY

from haphilipsjs import PairingFailure
import pytest

from homeassistant import config_entries
from homeassistant.components.philips_js.const import CONF_ALLOW_NOTIFY, DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from . import (
    MOCK_CONFIG,
    MOCK_CONFIG_PAIRED,
    MOCK_HOSTNAME,
    MOCK_NAME,
    MOCK_PASSWORD,
    MOCK_SYSTEM,
    MOCK_SYSTEM_UNPAIRED,
    MOCK_USERINPUT,
    MOCK_USERNAME,
)

from tests.common import MockConfigEntry

pytestmark = pytest.mark.usefixtures("mock_setup_entry")


@pytest.fixture
async def mock_tv_pairable(mock_tv):
    """Return a mock tv that is pariable."""
    mock_tv.system = MOCK_SYSTEM_UNPAIRED
    mock_tv.pairing_type = "digest_auth_pairing"
    mock_tv.api_version = 6
    mock_tv.api_version_detected = 6
    mock_tv.secured_transport = True
    mock_tv.name = MOCK_NAME

    mock_tv.pairRequest.return_value = {}
    mock_tv.pairGrant.return_value = MOCK_USERNAME, MOCK_PASSWORD
    return mock_tv


async def test_form(hass: HomeAssistant, mock_setup_entry) -> None:
    """Test we get the form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        MOCK_USERINPUT,
    )
    await hass.async_block_till_done()

    assert result2["type"] is FlowResultType.CREATE_ENTRY
    assert result2["title"] == "Philips TV (1234567890)"
    assert result2["data"] == MOCK_CONFIG
    assert len(mock_setup_entry.mock_calls) == 1


async def test_reauth(
    hass: HomeAssistant, mock_setup_entry, mock_config_entry: MockConfigEntry, mock_tv
) -> None:
    """Test we get the form."""

    mock_tv.system = MOCK_SYSTEM | {"model": "changed"}

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert len(mock_setup_entry.mock_calls) == 1

    result = await mock_config_entry.start_reauth_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        MOCK_USERINPUT,
    )
    await hass.async_block_till_done()

    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "reauth_successful"
    assert mock_config_entry.data == MOCK_CONFIG | {"system": mock_tv.system}
    assert len(mock_setup_entry.mock_calls) == 2


async def test_form_cannot_connect(hass: HomeAssistant, mock_tv) -> None:
    """Test we handle cannot connect error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    mock_tv.system = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], MOCK_USERINPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_pairing(hass: HomeAssistant, mock_tv_pairable, mock_setup_entry) -> None:
    """Test we get the form."""
    mock_tv = mock_tv_pairable

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        MOCK_USERINPUT,
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    mock_tv.setTransport.assert_called_with(True)
    mock_tv.pairRequest.assert_called()

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pin": "1234"}
    )

    assert result == {
        "context": {
            "source": "user",
            "unique_id": "ABCDEFGHIJKLF",
            "title_placeholders": {
                "name": "Philips TV",
            },
        },
        "flow_id": ANY,
        "type": "create_entry",
        "description": None,
        "description_placeholders": None,
        "handler": "philips_js",
        "result": ANY,
        "title": "55PUS7181/12 (ABCDEFGHIJKLF)",
        "data": MOCK_CONFIG_PAIRED,
        "version": 1,
        "options": {},
        "minor_version": 1,
        "subentries": (),
    }

    await hass.async_block_till_done()
    assert len(mock_setup_entry.mock_calls) == 1


async def test_pair_request_failed(
    hass: HomeAssistant, mock_tv_pairable, mock_setup_entry
) -> None:
    """Test we get the form."""
    mock_tv = mock_tv_pairable
    mock_tv.pairRequest.side_effect = PairingFailure({})

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        MOCK_USERINPUT,
    )

    assert result == {
        "flow_id": ANY,
        "description_placeholders": {"error_id": None},
        "handler": "philips_js",
        "reason": "pairing_failure",
        "type": "abort",
    }


async def test_pair_grant_failed(
    hass: HomeAssistant, mock_tv_pairable, mock_setup_entry
) -> None:
    """Test we get the form."""
    mock_tv = mock_tv_pairable

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        MOCK_USERINPUT,
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    mock_tv.setTransport.assert_called_with(True)
    mock_tv.pairRequest.assert_called()

    # Test with invalid pin
    mock_tv.pairGrant.side_effect = PairingFailure({"error_id": "INVALID_PIN"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pin": "1234"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"pin": "invalid_pin"}

    # Test with unexpected failure
    mock_tv.pairGrant.side_effect = PairingFailure({})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pin": "1234"}
    )

    assert result == {
        "flow_id": ANY,
        "description_placeholders": {"error_id": None},
        "handler": "philips_js",
        "reason": "pairing_failure",
        "type": "abort",
    }


async def test_options_flow(hass: HomeAssistant) -> None:
    """Test config flow options."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="123456",
        data=MOCK_CONFIG_PAIRED,
    )
    config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_ALLOW_NOTIFY: True}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {CONF_ALLOW_NOTIFY: True}


@pytest.mark.parametrize(
    ("secured_transport", "discovery_type"),
    [(True, "_philipstv_s_rpc._tcp.local."), (False, "_philipstv_rpc._tcp.local.")],
)
async def test_zeroconf_discovery(
    hass: HomeAssistant, mock_tv_pairable, secured_transport, discovery_type
) -> None:
    """Test we can setup from zeroconf discovery."""

    mock_tv_pairable.secured_transport = secured_transport
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=ZeroconfServiceInfo(
            ip_address=ip_address("127.0.0.1"),
            ip_addresses=[ip_address("127.0.0.1")],
            hostname=MOCK_HOSTNAME,
            name=MOCK_NAME,
            port=None,
            properties={},
            type=discovery_type,
        ),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] is None

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    mock_tv_pairable.setTransport.assert_called_with(secured_transport)
    mock_tv_pairable.pairRequest.assert_called()


async def test_zeroconf_probe_failed(
    hass: HomeAssistant,
    mock_tv_pairable,
) -> None:
    """Test we can setup from zeroconf discovery."""

    mock_tv_pairable.system = None

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=ZeroconfServiceInfo(
            ip_address=ip_address("127.0.0.1"),
            ip_addresses=[ip_address("127.0.0.1")],
            hostname=MOCK_HOSTNAME,
            name=MOCK_NAME,
            port=None,
            properties={},
            type="_philipstv_s_rpc._tcp.local.",
        ),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "discovery_failure"
