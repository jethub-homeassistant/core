"""Support for selects which integrates with other components."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.select import (
    ATTR_OPTION,
    ATTR_OPTIONS,
    DOMAIN as SELECT_DOMAIN,
    SelectEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_NAME,
    CONF_OPTIMISTIC,
    CONF_STATE,
    CONF_UNIQUE_ID,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, selector
from homeassistant.helpers.device import async_device_info_to_link_from_device_id
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
    AddEntitiesCallback,
)
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import TriggerUpdateCoordinator
from .const import DOMAIN
from .template_entity import TemplateEntity, make_template_entity_common_modern_schema
from .trigger_entity import TriggerEntity

_LOGGER = logging.getLogger(__name__)

CONF_OPTIONS = "options"
CONF_SELECT_OPTION = "select_option"

DEFAULT_NAME = "Template Select"
DEFAULT_OPTIMISTIC = False

SELECT_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_STATE): cv.template,
        vol.Required(CONF_SELECT_OPTION): cv.SCRIPT_SCHEMA,
        vol.Required(ATTR_OPTIONS): cv.template,
        vol.Optional(CONF_OPTIMISTIC, default=DEFAULT_OPTIMISTIC): cv.boolean,
    }
).extend(make_template_entity_common_modern_schema(DEFAULT_NAME).schema)


SELECT_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): cv.template,
        vol.Required(CONF_STATE): cv.template,
        vol.Required(CONF_OPTIONS): cv.template,
        vol.Optional(CONF_SELECT_OPTION): cv.SCRIPT_SCHEMA,
        vol.Optional(CONF_DEVICE_ID): selector.DeviceSelector(),
    }
)


async def _async_create_entities(
    hass: HomeAssistant, definitions: list[dict[str, Any]], unique_id_prefix: str | None
) -> list[TemplateSelect]:
    """Create the Template select."""
    entities = []
    for definition in definitions:
        unique_id = definition.get(CONF_UNIQUE_ID)
        if unique_id and unique_id_prefix:
            unique_id = f"{unique_id_prefix}-{unique_id}"
        entities.append(TemplateSelect(hass, definition, unique_id))
    return entities


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the template select."""
    if discovery_info is None:
        _LOGGER.warning(
            "Template select entities can only be configured under template:"
        )
        return

    if "coordinator" in discovery_info:
        async_add_entities(
            TriggerSelectEntity(hass, discovery_info["coordinator"], config)
            for config in discovery_info["entities"]
        )
        return

    async_add_entities(
        await _async_create_entities(
            hass, discovery_info["entities"], discovery_info["unique_id"]
        )
    )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Initialize config entry."""
    _options = dict(config_entry.options)
    _options.pop("template_type")
    validated_config = SELECT_CONFIG_SCHEMA(_options)
    async_add_entities([TemplateSelect(hass, validated_config, config_entry.entry_id)])


class TemplateSelect(TemplateEntity, SelectEntity):
    """Representation of a template select."""

    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        config: dict[str, Any],
        unique_id: str | None,
    ) -> None:
        """Initialize the select."""
        super().__init__(hass, config=config, unique_id=unique_id)
        assert self._attr_name is not None
        self._value_template = config[CONF_STATE]
        # Scripts can be an empty list, therefore we need to check for None
        if (select_option := config.get(CONF_SELECT_OPTION)) is not None:
            self.add_script(CONF_SELECT_OPTION, select_option, self._attr_name, DOMAIN)
        self._options_template = config[ATTR_OPTIONS]
        self._attr_assumed_state = self._optimistic = config.get(CONF_OPTIMISTIC, False)
        self._attr_options = []
        self._attr_current_option = None
        self._attr_device_info = async_device_info_to_link_from_device_id(
            hass,
            config.get(CONF_DEVICE_ID),
        )

    @callback
    def _async_setup_templates(self) -> None:
        """Set up templates."""
        self.add_template_attribute(
            "_attr_current_option",
            self._value_template,
            validator=cv.string,
            none_on_template_error=True,
        )
        self.add_template_attribute(
            "_attr_options",
            self._options_template,
            validator=vol.All(cv.ensure_list, [cv.string]),
            none_on_template_error=True,
        )
        super()._async_setup_templates()

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        if self._optimistic:
            self._attr_current_option = option
            self.async_write_ha_state()
        if select_option := self._action_scripts.get(CONF_SELECT_OPTION):
            await self.async_run_script(
                select_option,
                run_variables={ATTR_OPTION: option},
                context=self._context,
            )


class TriggerSelectEntity(TriggerEntity, SelectEntity):
    """Select entity based on trigger data."""

    domain = SELECT_DOMAIN
    extra_template_keys = (CONF_STATE,)
    extra_template_keys_complex = (ATTR_OPTIONS,)

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: TriggerUpdateCoordinator,
        config: dict,
    ) -> None:
        """Initialize the entity."""
        super().__init__(hass, coordinator, config)
        # Scripts can be an empty list, therefore we need to check for None
        if (select_option := config.get(CONF_SELECT_OPTION)) is not None:
            self.add_script(
                CONF_SELECT_OPTION,
                select_option,
                self._rendered.get(CONF_NAME, DEFAULT_NAME),
                DOMAIN,
            )

    @property
    def current_option(self) -> str | None:
        """Return the currently selected option."""
        return self._rendered.get(CONF_STATE)

    @property
    def options(self) -> list[str]:
        """Return the list of available options."""
        return self._rendered.get(ATTR_OPTIONS, [])

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        if self._config[CONF_OPTIMISTIC]:
            self._attr_current_option = option
            self.async_write_ha_state()
        if select_option := self._action_scripts.get(CONF_SELECT_OPTION):
            await self.async_run_script(
                select_option,
                run_variables={ATTR_OPTION: option},
                context=self._context,
            )
