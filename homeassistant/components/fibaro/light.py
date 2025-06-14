"""Support for Fibaro lights."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from pyfibaro.fibaro_device import DeviceModel

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ENTITY_ID_FORMAT,
    ColorMode,
    LightEntity,
    brightness_supported,
    color_supported,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import FibaroConfigEntry
from .entity import FibaroEntity

PARALLEL_UPDATES = 2


def scaleto255(value: int | None) -> int:
    """Scale the input value from 0-100 to 0-255."""
    if value is None:
        return 0
    # Fibaro has a funny way of storing brightness either 0-100 or 0-99
    # depending on device type (e.g. dimmer vs led)
    if value > 98:
        value = 100
    return round(value * 2.55)


def scaleto99(value: int | None) -> int:
    """Scale the input value from 0-255 to 0-99."""
    if value is None:
        return 0
    # Make sure a low but non-zero value is not rounded down to zero
    if 0 < value < 3:
        return 1
    return min(round(value / 2.55), 99)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FibaroConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Perform the setup for Fibaro controller devices."""
    controller = entry.runtime_data
    async_add_entities(
        [FibaroLight(device) for device in controller.fibaro_devices[Platform.LIGHT]],
        True,
    )


class FibaroLight(FibaroEntity, LightEntity):
    """Representation of a Fibaro Light, including dimmable."""

    def __init__(self, fibaro_device: DeviceModel) -> None:
        """Initialize the light."""
        supports_color = (
            "color" in fibaro_device.properties
            or "colorComponents" in fibaro_device.properties
            or "RGB" in fibaro_device.type
            or "rgb" in fibaro_device.type
            or "color" in fibaro_device.base_type
        ) and (
            "setColor" in fibaro_device.actions
            or "setColorComponents" in fibaro_device.actions
        )
        supports_white_v = (
            "setW" in fibaro_device.actions
            or "RGBW" in fibaro_device.type
            or "rgbw" in fibaro_device.type
        )
        supports_dimming = (
            fibaro_device.has_interface("levelChange")
            or fibaro_device.type == "com.fibaro.multilevelSwitch"
        ) and "setValue" in fibaro_device.actions

        if supports_color and supports_white_v:
            self._attr_supported_color_modes = {ColorMode.RGBW}
            self._attr_color_mode = ColorMode.RGBW
        elif supports_color:
            self._attr_supported_color_modes = {ColorMode.RGB}
            self._attr_color_mode = ColorMode.RGB
        elif supports_dimming:
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_color_mode = ColorMode.BRIGHTNESS
        else:
            self._attr_supported_color_modes = {ColorMode.ONOFF}
            self._attr_color_mode = ColorMode.ONOFF

        super().__init__(fibaro_device)
        self.entity_id = ENTITY_ID_FORMAT.format(self.ha_id)

    def turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        if ATTR_BRIGHTNESS in kwargs:
            self._attr_brightness = kwargs[ATTR_BRIGHTNESS]
            self.set_level(scaleto99(self._attr_brightness))
            return

        if ATTR_RGB_COLOR in kwargs:
            # Update based on parameters
            rgb = kwargs[ATTR_RGB_COLOR]
            self._attr_rgb_color = rgb
            self.call_set_color(int(rgb[0]), int(rgb[1]), int(rgb[2]), 0)
            return

        if ATTR_RGBW_COLOR in kwargs:
            # Update based on parameters
            rgbw = kwargs[ATTR_RGBW_COLOR]
            self._attr_rgbw_color = rgbw
            self.call_set_color(int(rgbw[0]), int(rgbw[1]), int(rgbw[2]), int(rgbw[3]))
            return

        # The simplest case is left for last. No dimming, just switch on
        self.call_turn_on()

    def turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        self.call_turn_off()

    def update(self) -> None:
        """Update the state."""
        super().update()

        # Dimmable and RGB lights can be on based on different
        # properties, so we need to check here several values
        # to see if the light is on.
        light_is_on = self.current_binary_state
        with suppress(TypeError):
            if self.fibaro_device.brightness != 0:
                light_is_on = True
        with suppress(TypeError):
            if self.fibaro_device.current_program != 0:
                light_is_on = True
        with suppress(TypeError):
            if self.fibaro_device.current_program_id != 0:
                light_is_on = True
        self._attr_is_on = light_is_on

        # Brightness handling
        if brightness_supported(self.supported_color_modes):
            self._attr_brightness = scaleto255(self.fibaro_device.value.int_value())

        # Color handling
        if (
            color_supported(self.supported_color_modes)
            and self.fibaro_device.color.has_color
        ):
            # Fibaro communicates the color as an 'R, G, B, W' string
            rgbw = self.fibaro_device.color.rgbw_color
            if rgbw == (0, 0, 0, 0) and self.fibaro_device.last_color_set.has_color:
                rgbw = self.fibaro_device.last_color_set.rgbw_color

            if self.color_mode == ColorMode.RGB:
                self._attr_rgb_color = rgbw[:3]
            else:
                self._attr_rgbw_color = rgbw
