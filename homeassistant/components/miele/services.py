"""Services for Miele integration."""

import logging
from typing import cast

import aiohttp
import voluptuous as vol

from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.service import async_extract_config_entry_ids

from .const import DOMAIN
from .coordinator import MieleConfigEntry

ATTR_PROGRAM_ID = "program_id"
ATTR_DURATION = "duration"


SERVICE_SET_PROGRAM = "set_program"
SERVICE_SET_PROGRAM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): str,
        vol.Required(ATTR_PROGRAM_ID): cv.positive_int,
    },
)

_LOGGER = logging.getLogger(__name__)


async def _extract_config_entry(service_call: ServiceCall) -> MieleConfigEntry:
    """Extract config entry from the service call."""
    hass = service_call.hass
    target_entry_ids = await async_extract_config_entry_ids(hass, service_call)
    target_entries: list[MieleConfigEntry] = [
        loaded_entry
        for loaded_entry in hass.config_entries.async_loaded_entries(DOMAIN)
        if loaded_entry.entry_id in target_entry_ids
    ]
    if not target_entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_target",
        )
    return target_entries[0]


async def set_program(call: ServiceCall) -> None:
    """Set a program on a Miele appliance."""

    _LOGGER.debug("Set program call: %s", call)
    config_entry = await _extract_config_entry(call)
    device_reg = dr.async_get(call.hass)
    api = config_entry.runtime_data.api
    device = call.data[ATTR_DEVICE_ID]
    device_entry = device_reg.async_get(device)

    data = {"programId": call.data[ATTR_PROGRAM_ID]}
    serial_number = next(
        (
            identifier[1]
            for identifier in cast(dr.DeviceEntry, device_entry).identifiers
            if identifier[0] == DOMAIN
        ),
        None,
    )
    if serial_number is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_target",
        )
    try:
        await api.set_program(serial_number, data)
    except aiohttp.ClientResponseError as ex:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="set_program_error",
            translation_placeholders={
                "status": str(ex.status),
                "message": ex.message,
            },
        ) from ex


async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up services."""

    hass.services.async_register(
        DOMAIN, SERVICE_SET_PROGRAM, set_program, SERVICE_SET_PROGRAM_SCHEMA
    )
