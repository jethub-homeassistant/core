"""Config flow for Vodafone Station integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aiovodafone import VodafoneStationSercommApi, exceptions as aiovodafone_exceptions
import voluptuous as vol

from homeassistant.components.device_tracker import (
    CONF_CONSIDER_HOME,
    DEFAULT_CONSIDER_HOME,
)
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback

from .const import _LOGGER, DEFAULT_HOST, DEFAULT_USERNAME, DOMAIN
from .coordinator import VodafoneConfigEntry
from .utils import async_client_session


def user_form_schema(user_input: dict[str, Any] | None) -> vol.Schema:
    """Return user form schema."""
    user_input = user_input or {}
    return vol.Schema(
        {
            vol.Optional(CONF_HOST, default=DEFAULT_HOST): str,
            vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
        }
    )


STEP_REAUTH_DATA_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Validate the user input allows us to connect."""

    session = await async_client_session(hass)
    api = VodafoneStationSercommApi(
        data[CONF_HOST], data[CONF_USERNAME], data[CONF_PASSWORD], session
    )

    try:
        await api.login()
    finally:
        await api.logout()

    return {"title": data[CONF_HOST]}


class VodafoneStationConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Vodafone Station."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: VodafoneConfigEntry,
    ) -> VodafoneStationOptionsFlowHandler:
        """Get the options flow for this handler."""
        return VodafoneStationOptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=user_form_schema(user_input)
            )

        # Use host because no serial number or mac is available to use for a unique id
        self._async_abort_entries_match({CONF_HOST: user_input[CONF_HOST]})

        errors = {}

        try:
            info = await validate_input(self.hass, user_input)
        except aiovodafone_exceptions.AlreadyLogged:
            errors["base"] = "already_logged"
        except aiovodafone_exceptions.CannotConnect:
            errors["base"] = "cannot_connect"
        except aiovodafone_exceptions.CannotAuthenticate:
            errors["base"] = "invalid_auth"
        except aiovodafone_exceptions.ModelNotSupported:
            errors["base"] = "model_not_supported"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        else:
            return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=user_form_schema(user_input), errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauth flow."""
        self.context["title_placeholders"] = {"host": entry_data[CONF_HOST]}
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reauth confirm."""
        errors = {}

        reauth_entry = self._get_reauth_entry()
        if user_input is not None:
            try:
                await validate_input(self.hass, {**reauth_entry.data, **user_input})
            except aiovodafone_exceptions.AlreadyLogged:
                errors["base"] = "already_logged"
            except aiovodafone_exceptions.CannotConnect:
                errors["base"] = "cannot_connect"
            except aiovodafone_exceptions.CannotAuthenticate:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            description_placeholders={CONF_HOST: reauth_entry.data[CONF_HOST]},
            data_schema=STEP_REAUTH_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of the device."""
        reconfigure_entry = self._get_reconfigure_entry()
        if not user_input:
            return self.async_show_form(
                step_id="reconfigure", data_schema=user_form_schema(user_input)
            )

        updated_host = user_input[CONF_HOST]

        if reconfigure_entry.data[CONF_HOST] != updated_host:
            self._async_abort_entries_match({CONF_HOST: updated_host})

        errors: dict[str, str] = {}

        try:
            await validate_input(self.hass, user_input)
        except aiovodafone_exceptions.AlreadyLogged:
            errors["base"] = "already_logged"
        except aiovodafone_exceptions.CannotConnect:
            errors["base"] = "cannot_connect"
        except aiovodafone_exceptions.CannotAuthenticate:
            errors["base"] = "invalid_auth"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        else:
            return self.async_update_reload_and_abort(
                reconfigure_entry, data_updates={CONF_HOST: updated_host}
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=user_form_schema(user_input),
            errors=errors,
        )


class VodafoneStationOptionsFlowHandler(OptionsFlowWithReload):
    """Handle a option flow."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle options flow."""

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_CONSIDER_HOME,
                    default=self.config_entry.options.get(
                        CONF_CONSIDER_HOME, DEFAULT_CONSIDER_HOME.total_seconds()
                    ),
                ): vol.All(vol.Coerce(int), vol.Clamp(min=0, max=900))
            }
        )
        return self.async_show_form(step_id="init", data_schema=data_schema)
