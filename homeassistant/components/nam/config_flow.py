"""Adds config flow for Nettigo Air Monitor."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from aiohttp.client_exceptions import ClientConnectorError
from nettigo_air_monitor import (
    ApiError,
    AuthFailedError,
    CannotGetMacError,
    ConnectionOptions,
    NettigoAirMonitor,
)
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

AUTH_SCHEMA = vol.Schema(
    {vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}
)


async def async_get_nam(
    hass: HomeAssistant, host: str, data: dict[str, Any]
) -> NettigoAirMonitor:
    """Get NAM client."""
    websession = async_get_clientsession(hass)
    options = ConnectionOptions(host, data.get(CONF_USERNAME), data.get(CONF_PASSWORD))

    return await NettigoAirMonitor.create(websession, options)


class NAMFlowHandler(ConfigFlow, domain=DOMAIN):
    """Config flow for Nettigo Air Monitor."""

    VERSION = 1

    host: str
    auth_enabled: bool = False

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self.host = user_input[CONF_HOST]

            try:
                nam = await async_get_nam(self.hass, self.host, {})
            except (ApiError, ClientConnectorError, TimeoutError):
                errors["base"] = "cannot_connect"
            except CannotGetMacError:
                return self.async_abort(reason="device_unsupported")
            except AuthFailedError:
                return await self.async_step_credentials()
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(format_mac(nam.mac))
                self._abort_if_unique_id_configured({CONF_HOST: self.host})

                return self.async_create_entry(
                    title=self.host,
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the credentials step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                nam = await async_get_nam(self.hass, self.host, user_input)
            except AuthFailedError:
                errors["base"] = "invalid_auth"
            except (ApiError, ClientConnectorError, TimeoutError):
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(format_mac(nam.mac))
                self._abort_if_unique_id_configured({CONF_HOST: self.host})

                return self.async_create_entry(
                    title=self.host,
                    data={**user_input, CONF_HOST: self.host},
                )

        return self.async_show_form(
            step_id="credentials", data_schema=AUTH_SCHEMA, errors=errors
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle zeroconf discovery."""
        self.host = discovery_info.host
        self.context["title_placeholders"] = {"host": self.host}

        # Do not probe the device if the host is already configured
        self._async_abort_entries_match({CONF_HOST: self.host})

        try:
            nam = await async_get_nam(self.hass, self.host, {})
        except (ApiError, ClientConnectorError, TimeoutError):
            return self.async_abort(reason="cannot_connect")
        except CannotGetMacError:
            return self.async_abort(reason="device_unsupported")
        except AuthFailedError:
            self.auth_enabled = True
            return await self.async_step_confirm_discovery()

        await self.async_set_unique_id(format_mac(nam.mac))

        return await self.async_step_confirm_discovery()

    async def async_step_confirm_discovery(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle discovery confirm."""
        errors: dict[str, str] = {}

        if user_input is not None:
            return self.async_create_entry(
                title=self.host,
                data={CONF_HOST: self.host},
            )

        if self.auth_enabled is True:
            return await self.async_step_credentials()

        self._set_confirm_only()

        return self.async_show_form(
            step_id="confirm_discovery",
            description_placeholders={"host": self.host},
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle configuration by re-auth."""
        self.host = entry_data[CONF_HOST]
        self.context["title_placeholders"] = {"host": self.host}
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Dialog that informs the user that reauth is required."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await async_get_nam(self.hass, self.host, user_input)
            except (
                ApiError,
                AuthFailedError,
                ClientConnectorError,
                TimeoutError,
            ):
                return self.async_abort(reason="reauth_unsuccessful")

            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data={**user_input, CONF_HOST: self.host}
            )

        return self.async_show_form(
            step_id="reauth_confirm",
            description_placeholders={"host": self.host},
            data_schema=AUTH_SCHEMA,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a reconfiguration flow initialized by the user."""
        errors = {}
        reconfigure_entry = self._get_reconfigure_entry()
        self.host = reconfigure_entry.data[CONF_HOST]

        if user_input is not None:
            try:
                nam = await async_get_nam(self.hass, user_input[CONF_HOST], {})
            except (ApiError, ClientConnectorError, TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(format_mac(nam.mac))
                self._abort_if_unique_id_mismatch(reason="another_device")

                return self.async_update_reload_and_abort(
                    reconfigure_entry, data_updates={CONF_HOST: user_input[CONF_HOST]}
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=self.host): str,
                }
            ),
            description_placeholders={"device_name": reconfigure_entry.title},
            errors=errors,
        )
