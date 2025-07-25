"""Config flow for Roku."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from rokuecp import Roku, RokuError
import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.ssdp import (
    ATTR_UPNP_FRIENDLY_NAME,
    ATTR_UPNP_SERIAL,
    SsdpServiceInfo,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .const import CONF_PLAY_MEDIA_APP_ID, DEFAULT_PLAY_MEDIA_APP_ID, DOMAIN
from .coordinator import RokuConfigEntry

DATA_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})

ERROR_CANNOT_CONNECT = "cannot_connect"
ERROR_UNKNOWN = "unknown"

_LOGGER = logging.getLogger(__name__)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from DATA_SCHEMA with values provided by the user.
    """
    session = async_get_clientsession(hass)
    roku = Roku(data[CONF_HOST], session=session)
    device = await roku.update()

    return {
        "title": device.info.name,
        "serial_number": device.info.serial_number,
    }


class RokuConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a Roku config flow."""

    VERSION = 1

    discovery_info: dict[str, Any]

    def __init__(self) -> None:
        """Set up the instance."""
        self.discovery_info = {}

    @callback
    def _show_form(
        self,
        user_input: dict[str, Any] | None,
        errors: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Show the form to the user."""
        suggested_values = user_input
        if suggested_values is None and self.source == SOURCE_RECONFIGURE:
            suggested_values = {
                CONF_HOST: self._get_reconfigure_entry().data[CONF_HOST]
            }

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                DATA_SCHEMA, suggested_values
            ),
            errors=errors or {},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of the integration."""
        return await self.async_step_user(user_input)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""
        if not user_input:
            return self._show_form(user_input)

        errors = {}

        try:
            info = await validate_input(self.hass, user_input)
        except RokuError:
            _LOGGER.debug("Roku Error", exc_info=True)
            errors["base"] = ERROR_CANNOT_CONNECT
            return self._show_form(user_input, errors)
        except Exception:
            _LOGGER.exception("Unknown error trying to connect")
            return self.async_abort(reason=ERROR_UNKNOWN)

        await self.async_set_unique_id(info["serial_number"])

        if self.source == SOURCE_RECONFIGURE:
            self._abort_if_unique_id_mismatch(reason="wrong_device")
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(),
                data_updates={CONF_HOST: user_input[CONF_HOST]},
            )

        self._abort_if_unique_id_configured()

        return self.async_create_entry(title=info["title"], data=user_input)

    async def async_step_homekit(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle a flow initialized by homekit discovery."""

        # If we already have the host configured do
        # not open connections to it if we can avoid it.
        self._async_abort_entries_match({CONF_HOST: discovery_info.host})

        self.discovery_info.update({CONF_HOST: discovery_info.host})

        try:
            info = await validate_input(self.hass, self.discovery_info)
        except RokuError:
            _LOGGER.debug("Roku Error", exc_info=True)
            return self.async_abort(reason=ERROR_CANNOT_CONNECT)
        except Exception:
            _LOGGER.exception("Unknown error trying to connect")
            return self.async_abort(reason=ERROR_UNKNOWN)

        await self.async_set_unique_id(info["serial_number"])
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: discovery_info.host},
        )

        self.context.update({"title_placeholders": {"name": info["title"]}})
        self.discovery_info.update({CONF_NAME: info["title"]})

        return await self.async_step_discovery_confirm()

    async def async_step_ssdp(
        self, discovery_info: SsdpServiceInfo
    ) -> ConfigFlowResult:
        """Handle a flow initialized by discovery."""
        host = urlparse(discovery_info.ssdp_location).hostname
        name = discovery_info.upnp[ATTR_UPNP_FRIENDLY_NAME]
        serial_number = discovery_info.upnp[ATTR_UPNP_SERIAL]

        await self.async_set_unique_id(serial_number)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        self.context.update({"title_placeholders": {"name": name}})

        self.discovery_info.update({CONF_HOST: host, CONF_NAME: name})

        try:
            await validate_input(self.hass, self.discovery_info)
        except RokuError:
            _LOGGER.debug("Roku Error", exc_info=True)
            return self.async_abort(reason=ERROR_CANNOT_CONNECT)
        except Exception:
            _LOGGER.exception("Unknown error trying to connect")
            return self.async_abort(reason=ERROR_UNKNOWN)

        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Handle user-confirmation of discovered device."""
        if user_input is None:
            return self.async_show_form(
                step_id="discovery_confirm",
                description_placeholders={"name": self.discovery_info[CONF_NAME]},
                errors={},
            )

        return self.async_create_entry(
            title=self.discovery_info[CONF_NAME],
            data=self.discovery_info,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: RokuConfigEntry,
    ) -> RokuOptionsFlowHandler:
        """Create the options flow."""
        return RokuOptionsFlowHandler()


class RokuOptionsFlowHandler(OptionsFlowWithReload):
    """Handle Roku options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage Roku options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_PLAY_MEDIA_APP_ID,
                        default=self.config_entry.options.get(
                            CONF_PLAY_MEDIA_APP_ID, DEFAULT_PLAY_MEDIA_APP_ID
                        ),
                    ): str,
                }
            ),
        )
