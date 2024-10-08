"""Define tests for the GeoNet NZ Volcano config flow."""

from datetime import timedelta
from unittest.mock import patch

from homeassistant.components.geonetnz_volcano import DOMAIN
from homeassistant.config_entries import SOURCE_IMPORT, SOURCE_USER
from homeassistant.const import (
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_RADIUS,
    CONF_SCAN_INTERVAL,
    CONF_UNIT_SYSTEM,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType


async def test_duplicate_error(hass: HomeAssistant, config_entry) -> None:
    """Test that errors are shown when duplicates are added."""
    conf = {CONF_LATITUDE: -41.2, CONF_LONGITUDE: 174.7, CONF_RADIUS: 25}

    config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=conf
    )
    assert result["errors"] == {"base": "already_configured"}


async def test_show_form(hass: HomeAssistant) -> None:
    """Test that the form is served with no input."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=None
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_step_import(hass: HomeAssistant) -> None:
    """Test that the import step works."""
    conf = {
        CONF_LATITUDE: -41.2,
        CONF_LONGITUDE: 174.7,
        CONF_RADIUS: 25,
        CONF_UNIT_SYSTEM: "metric",
        CONF_SCAN_INTERVAL: timedelta(minutes=4),
    }

    with (
        patch(
            "homeassistant.components.geonetnz_volcano.async_setup_entry",
            return_value=True,
        ),
        patch(
            "homeassistant.components.geonetnz_volcano.async_setup", return_value=True
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=conf
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "-41.2, 174.7"
    assert result["data"] == {
        CONF_LATITUDE: -41.2,
        CONF_LONGITUDE: 174.7,
        CONF_RADIUS: 25,
        CONF_UNIT_SYSTEM: "metric",
        CONF_SCAN_INTERVAL: 240.0,
    }


async def test_step_user(hass: HomeAssistant) -> None:
    """Test that the user step works."""
    hass.config.latitude = -41.2
    hass.config.longitude = 174.7
    conf = {CONF_RADIUS: 25}

    with (
        patch(
            "homeassistant.components.geonetnz_volcano.async_setup_entry",
            return_value=True,
        ),
        patch(
            "homeassistant.components.geonetnz_volcano.async_setup", return_value=True
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}, data=conf
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "-41.2, 174.7"
    assert result["data"] == {
        CONF_LATITUDE: -41.2,
        CONF_LONGITUDE: 174.7,
        CONF_RADIUS: 25,
        CONF_UNIT_SYSTEM: "metric",
        CONF_SCAN_INTERVAL: 300.0,
    }
