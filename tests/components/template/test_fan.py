"""The tests for the Template fan platform."""

from typing import Any

import pytest
import voluptuous as vol

from homeassistant import setup
from homeassistant.components import fan
from homeassistant.components.fan import (
    ATTR_DIRECTION,
    ATTR_OSCILLATING,
    ATTR_PERCENTAGE,
    ATTR_PRESET_MODE,
    DIRECTION_FORWARD,
    DIRECTION_REVERSE,
    DOMAIN as FAN_DOMAIN,
    FanEntityFeature,
    NotValidPresetModeError,
)
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component

from .conftest import ConfigurationStyle

from tests.common import assert_setup_component
from tests.components.fan import common

_TEST_OBJECT_ID = "test_fan"
_TEST_FAN = f"fan.{_TEST_OBJECT_ID}"
# Represent for fan's state
_STATE_INPUT_BOOLEAN = "input_boolean.state"
# Represent for fan's state
_STATE_AVAILABILITY_BOOLEAN = "availability_boolean.state"
# Represent for fan's preset mode
_PRESET_MODE_INPUT_SELECT = "input_select.preset_mode"
# Represent for fan's speed percentage
_PERCENTAGE_INPUT_NUMBER = "input_number.percentage"
# Represent for fan's oscillating
_OSC_INPUT = "input_select.osc"
# Represent for fan's direction
_DIRECTION_INPUT_SELECT = "input_select.direction"


OPTIMISTIC_ON_OFF_CONFIG = {
    "turn_on": {
        "service": "test.automation",
        "data": {
            "action": "turn_on",
            "caller": "{{ this.entity_id }}",
        },
    },
    "turn_off": {
        "service": "test.automation",
        "data": {
            "action": "turn_off",
            "caller": "{{ this.entity_id }}",
        },
    },
}


PERCENTAGE_ACTION = {
    "set_percentage": {
        "action": "test.automation",
        "data": {
            "action": "set_percentage",
            "percentage": "{{ percentage }}",
            "caller": "{{ this.entity_id }}",
        },
    },
}
OPTIMISTIC_PERCENTAGE_CONFIG = {
    **OPTIMISTIC_ON_OFF_CONFIG,
    **PERCENTAGE_ACTION,
}

PRESET_MODE_ACTION = {
    "set_preset_mode": {
        "action": "test.automation",
        "data": {
            "action": "set_preset_mode",
            "preset_mode": "{{ preset_mode }}",
            "caller": "{{ this.entity_id }}",
        },
    },
}
OPTIMISTIC_PRESET_MODE_CONFIG = {
    **OPTIMISTIC_ON_OFF_CONFIG,
    **PRESET_MODE_ACTION,
}
OPTIMISTIC_PRESET_MODE_CONFIG2 = {
    **OPTIMISTIC_PRESET_MODE_CONFIG,
    "preset_modes": ["auto", "low", "medium", "high"],
}

OSCILLATE_ACTION = {
    "set_oscillating": {
        "action": "test.automation",
        "data": {
            "action": "set_oscillating",
            "oscillating": "{{ oscillating }}",
            "caller": "{{ this.entity_id }}",
        },
    },
}
OPTIMISTIC_OSCILLATE_CONFIG = {
    **OPTIMISTIC_ON_OFF_CONFIG,
    **OSCILLATE_ACTION,
}

DIRECTION_ACTION = {
    "set_direction": {
        "action": "test.automation",
        "data": {
            "action": "set_direction",
            "direction": "{{ direction }}",
            "caller": "{{ this.entity_id }}",
        },
    },
}
OPTIMISTIC_DIRECTION_CONFIG = {
    **OPTIMISTIC_ON_OFF_CONFIG,
    **DIRECTION_ACTION,
}


async def async_setup_legacy_format(
    hass: HomeAssistant, count: int, light_config: dict[str, Any]
) -> None:
    """Do setup of fan integration via legacy format."""
    config = {"fan": {"platform": "template", "fans": light_config}}

    with assert_setup_component(count, fan.DOMAIN):
        assert await async_setup_component(
            hass,
            fan.DOMAIN,
            config,
        )

    await hass.async_block_till_done()
    await hass.async_start()
    await hass.async_block_till_done()


async def async_setup_legacy_format_with_attribute(
    hass: HomeAssistant,
    count: int,
    attribute: str,
    attribute_template: str,
    extra_config: dict,
) -> None:
    """Do setup of a legacy fan that has a single templated attribute."""
    extra = {attribute: attribute_template} if attribute and attribute_template else {}
    await async_setup_legacy_format(
        hass,
        count,
        {
            _TEST_OBJECT_ID: {
                **extra_config,
                "value_template": "{{ 1 == 1 }}",
                **extra,
            }
        },
    )


@pytest.fixture
async def setup_fan(
    hass: HomeAssistant,
    count: int,
    style: ConfigurationStyle,
    light_config: dict[str, Any],
) -> None:
    """Do setup of fan integration."""
    if style == ConfigurationStyle.LEGACY:
        await async_setup_legacy_format(hass, count, light_config)


@pytest.fixture
async def setup_test_fan_with_extra_config(
    hass: HomeAssistant,
    count: int,
    style: ConfigurationStyle,
    fan_config: dict[str, Any],
    extra_config: dict[str, Any],
) -> None:
    """Do setup of fan integration."""
    config = {_TEST_OBJECT_ID: {**fan_config, **extra_config}}
    if style == ConfigurationStyle.LEGACY:
        await async_setup_legacy_format(hass, count, config)


@pytest.fixture
async def setup_optimistic_fan_attribute(
    hass: HomeAssistant,
    count: int,
    style: ConfigurationStyle,
    extra_config: dict,
) -> None:
    """Do setup of a non-optimistic fan with an optimistic attribute."""
    if style == ConfigurationStyle.LEGACY:
        await async_setup_legacy_format_with_attribute(
            hass, count, "", "", extra_config
        )


@pytest.mark.parametrize(("count", "domain"), [(1, FAN_DOMAIN)])
@pytest.mark.parametrize(
    "config",
    [
        {
            FAN_DOMAIN: {
                "platform": "template",
                "fans": {
                    "test_fan": {
                        "value_template": "{{ 'on' }}",
                        "turn_on": {"service": "script.fan_on"},
                        "turn_off": {"service": "script.fan_off"},
                    }
                },
            }
        },
    ],
)
@pytest.mark.usefixtures("start_ha")
async def test_missing_optional_config(hass: HomeAssistant) -> None:
    """Test: missing optional template is ok."""
    _verify(hass, STATE_ON, None, None, None, None)


@pytest.mark.parametrize(("count", "domain"), [(0, FAN_DOMAIN)])
@pytest.mark.parametrize(
    "config",
    [
        {
            FAN_DOMAIN: {
                "platform": "template",
                "fans": {
                    "platform": "template",
                    "fans": {
                        "test_fan": {
                            "turn_on": {"service": "script.fan_on"},
                            "turn_off": {"service": "script.fan_off"},
                        }
                    },
                },
            }
        },
        {
            FAN_DOMAIN: {
                "platform": "template",
                "fans": {
                    "platform": "template",
                    "fans": {
                        "test_fan": {
                            "value_template": "{{ 'on' }}",
                            "turn_off": {"service": "script.fan_off"},
                        }
                    },
                },
            }
        },
        {
            FAN_DOMAIN: {
                "platform": "template",
                "fans": {
                    "platform": "template",
                    "fans": {
                        "test_fan": {
                            "value_template": "{{ 'on' }}",
                            "turn_on": {"service": "script.fan_on"},
                        }
                    },
                },
            }
        },
    ],
)
@pytest.mark.usefixtures("start_ha")
async def test_wrong_template_config(hass: HomeAssistant) -> None:
    """Test: missing 'value_template' will fail."""
    assert hass.states.async_all("fan") == []


@pytest.mark.parametrize(("count", "domain"), [(1, FAN_DOMAIN)])
@pytest.mark.parametrize(
    "config",
    [
        {
            FAN_DOMAIN: {
                "platform": "template",
                "fans": {
                    "test_fan": {
                        "value_template": "{{ is_state('input_boolean.state', 'True') }}",
                        "percentage_template": (
                            "{{ states('input_number.percentage') }}"
                        ),
                        **OPTIMISTIC_ON_OFF_CONFIG,
                        **PERCENTAGE_ACTION,
                        "preset_mode_template": (
                            "{{ states('input_select.preset_mode') }}"
                        ),
                        **PRESET_MODE_ACTION,
                        "oscillating_template": "{{ states('input_select.osc') }}",
                        **OSCILLATE_ACTION,
                        "direction_template": "{{ states('input_select.direction') }}",
                        **DIRECTION_ACTION,
                        "speed_count": "3",
                    }
                },
            }
        },
    ],
)
@pytest.mark.usefixtures("start_ha")
async def test_templates_with_entities(hass: HomeAssistant) -> None:
    """Test tempalates with values from other entities."""
    _verify(hass, STATE_OFF, 0, None, None, None)

    hass.states.async_set(_STATE_INPUT_BOOLEAN, True)
    hass.states.async_set(_PERCENTAGE_INPUT_NUMBER, 66)
    hass.states.async_set(_OSC_INPUT, "True")

    for set_state, set_value, value in (
        (_DIRECTION_INPUT_SELECT, DIRECTION_FORWARD, 66),
        (_PERCENTAGE_INPUT_NUMBER, 33, 33),
        (_PERCENTAGE_INPUT_NUMBER, 66, 66),
        (_PERCENTAGE_INPUT_NUMBER, 100, 100),
        (_PERCENTAGE_INPUT_NUMBER, "dog", 0),
    ):
        hass.states.async_set(set_state, set_value)
        await hass.async_block_till_done()
        _verify(hass, STATE_ON, value, True, DIRECTION_FORWARD, None)

    hass.states.async_set(_STATE_INPUT_BOOLEAN, False)
    await hass.async_block_till_done()
    _verify(hass, STATE_OFF, 0, True, DIRECTION_FORWARD, None)


@pytest.mark.parametrize(("count", "domain"), [(1, FAN_DOMAIN)])
@pytest.mark.parametrize(
    ("config", "entity", "tests"),
    [
        (
            {
                FAN_DOMAIN: {
                    "platform": "template",
                    "fans": {
                        "test_fan": {
                            "value_template": "{{ 'on' }}",
                            "percentage_template": "{{ states('sensor.percentage') }}",
                            **OPTIMISTIC_PERCENTAGE_CONFIG,
                        },
                    },
                }
            },
            "sensor.percentage",
            [
                ("0", 0, None),
                ("33", 33, None),
                ("invalid", 0, None),
                ("5000", 0, None),
                ("100", 100, None),
                ("0", 0, None),
            ],
        ),
        (
            {
                FAN_DOMAIN: {
                    "platform": "template",
                    "fans": {
                        "test_fan": {
                            "value_template": "{{ 'on' }}",
                            "preset_modes": ["auto", "smart"],
                            "preset_mode_template": (
                                "{{ states('sensor.preset_mode') }}"
                            ),
                            **OPTIMISTIC_PRESET_MODE_CONFIG,
                        },
                    },
                }
            },
            "sensor.preset_mode",
            [
                ("0", None, None),
                ("invalid", None, None),
                ("auto", None, "auto"),
                ("smart", None, "smart"),
                ("invalid", None, None),
            ],
        ),
    ],
)
@pytest.mark.usefixtures("start_ha")
async def test_templates_with_entities2(hass: HomeAssistant, entity, tests) -> None:
    """Test templates with values from other entities."""
    for set_percentage, test_percentage, test_type in tests:
        hass.states.async_set(entity, set_percentage)
        await hass.async_block_till_done()
        _verify(hass, STATE_ON, test_percentage, None, None, test_type)


@pytest.mark.parametrize(("count", "domain"), [(1, FAN_DOMAIN)])
@pytest.mark.parametrize(
    "config",
    [
        {
            FAN_DOMAIN: {
                "platform": "template",
                "fans": {
                    "test_fan": {
                        "availability_template": (
                            "{{ is_state('availability_boolean.state', 'on') }}"
                        ),
                        "value_template": "{{ 'on' }}",
                        "oscillating_template": "{{ 1 == 1 }}",
                        "direction_template": "{{ 'forward' }}",
                        "turn_on": {"service": "script.fan_on"},
                        "turn_off": {"service": "script.fan_off"},
                    }
                },
            }
        },
    ],
)
@pytest.mark.usefixtures("start_ha")
async def test_availability_template_with_entities(hass: HomeAssistant) -> None:
    """Test availability tempalates with values from other entities."""
    for state, test_assert in ((STATE_ON, True), (STATE_OFF, False)):
        hass.states.async_set(_STATE_AVAILABILITY_BOOLEAN, state)
        await hass.async_block_till_done()
        assert (hass.states.get(_TEST_FAN).state != STATE_UNAVAILABLE) == test_assert


@pytest.mark.parametrize(("count", "domain"), [(1, FAN_DOMAIN)])
@pytest.mark.parametrize(
    ("config", "states"),
    [
        (
            {
                FAN_DOMAIN: {
                    "platform": "template",
                    "fans": {
                        "test_fan": {
                            "value_template": "{{ 'unavailable' }}",
                            **OPTIMISTIC_ON_OFF_CONFIG,
                        }
                    },
                }
            },
            [STATE_OFF, None, None, None],
        ),
        (
            {
                FAN_DOMAIN: {
                    "platform": "template",
                    "fans": {
                        "test_fan": {
                            "value_template": "{{ 'on' }}",
                            "percentage_template": "{{ 0 }}",
                            **OPTIMISTIC_PERCENTAGE_CONFIG,
                            "oscillating_template": "{{ 'unavailable' }}",
                            **OSCILLATE_ACTION,
                            "direction_template": "{{ 'unavailable' }}",
                            **DIRECTION_ACTION,
                        }
                    },
                }
            },
            [STATE_ON, 0, None, None],
        ),
        (
            {
                FAN_DOMAIN: {
                    "platform": "template",
                    "fans": {
                        "test_fan": {
                            "value_template": "{{ 'on' }}",
                            "percentage_template": "{{ 66 }}",
                            **OPTIMISTIC_PERCENTAGE_CONFIG,
                            "oscillating_template": "{{ 1 == 1 }}",
                            **OSCILLATE_ACTION,
                            "direction_template": "{{ 'forward' }}",
                            **DIRECTION_ACTION,
                        }
                    },
                }
            },
            [STATE_ON, 66, True, DIRECTION_FORWARD],
        ),
        (
            {
                FAN_DOMAIN: {
                    "platform": "template",
                    "fans": {
                        "test_fan": {
                            "value_template": "{{ 'abc' }}",
                            "percentage_template": "{{ 0 }}",
                            **OPTIMISTIC_PERCENTAGE_CONFIG,
                            "oscillating_template": "{{ 'xyz' }}",
                            **OSCILLATE_ACTION,
                            "direction_template": "{{ 'right' }}",
                            **DIRECTION_ACTION,
                        }
                    },
                }
            },
            [STATE_OFF, 0, None, None],
        ),
    ],
)
@pytest.mark.usefixtures("start_ha")
async def test_template_with_unavailable_entities(hass: HomeAssistant, states) -> None:
    """Test unavailability with value_template."""
    _verify(hass, states[0], states[1], states[2], states[3], None)


@pytest.mark.parametrize(("count", "domain"), [(1, FAN_DOMAIN)])
@pytest.mark.parametrize(
    "config",
    [
        {
            FAN_DOMAIN: {
                "platform": "template",
                "fans": {
                    "test_fan": {
                        "value_template": "{{ 'on' }}",
                        "availability_template": "{{ x - 12 }}",
                        "preset_mode_template": (
                            "{{ states('input_select.preset_mode') }}"
                        ),
                        "oscillating_template": "{{ states('input_select.osc') }}",
                        "direction_template": "{{ states('input_select.direction') }}",
                        "turn_on": {"service": "script.fan_on"},
                        "turn_off": {"service": "script.fan_off"},
                    }
                },
            }
        },
    ],
)
@pytest.mark.usefixtures("start_ha")
async def test_invalid_availability_template_keeps_component_available(
    hass: HomeAssistant, caplog_setup_text
) -> None:
    """Test that an invalid availability keeps the device available."""
    assert hass.states.get("fan.test_fan").state != STATE_UNAVAILABLE
    assert "TemplateError" in caplog_setup_text
    assert "x" in caplog_setup_text


async def test_on_off(hass: HomeAssistant, calls: list[ServiceCall]) -> None:
    """Test turn on and turn off."""
    await _register_components(hass)

    for expected_calls, (func, state, action) in enumerate(
        [
            (common.async_turn_on, STATE_ON, "turn_on"),
            (common.async_turn_off, STATE_OFF, "turn_off"),
        ]
    ):
        await func(hass, _TEST_FAN)
        assert hass.states.get(_STATE_INPUT_BOOLEAN).state == state
        _verify(hass, state, 0, None, None, None)
        assert len(calls) == expected_calls + 1
        assert calls[-1].data["action"] == action
        assert calls[-1].data["caller"] == _TEST_FAN


async def test_set_invalid_direction_from_initial_stage(
    hass: HomeAssistant, calls: list[ServiceCall]
) -> None:
    """Test set invalid direction when fan is in initial state."""
    await _register_components(hass)

    await common.async_turn_on(hass, _TEST_FAN)

    await common.async_set_direction(hass, _TEST_FAN, "invalid")

    assert hass.states.get(_DIRECTION_INPUT_SELECT).state == ""
    _verify(hass, STATE_ON, 0, None, None, None)


async def test_set_osc(hass: HomeAssistant, calls: list[ServiceCall]) -> None:
    """Test set oscillating."""
    await _register_components(hass)
    expected_calls = 0

    await common.async_turn_on(hass, _TEST_FAN)
    expected_calls += 1
    for state in (True, False):
        await common.async_oscillate(hass, _TEST_FAN, state)
        assert hass.states.get(_OSC_INPUT).state == str(state)
        _verify(hass, STATE_ON, 0, state, None, None)
        expected_calls += 1
        assert len(calls) == expected_calls
        assert calls[-1].data["action"] == "set_oscillating"
        assert calls[-1].data["caller"] == _TEST_FAN
        assert calls[-1].data["option"] == state


async def test_set_direction(hass: HomeAssistant, calls: list[ServiceCall]) -> None:
    """Test set valid direction."""
    await _register_components(hass)
    expected_calls = 0

    await common.async_turn_on(hass, _TEST_FAN)
    expected_calls += 1
    for cmd in (DIRECTION_FORWARD, DIRECTION_REVERSE):
        await common.async_set_direction(hass, _TEST_FAN, cmd)
        assert hass.states.get(_DIRECTION_INPUT_SELECT).state == cmd
        _verify(hass, STATE_ON, 0, None, cmd, None)
        expected_calls += 1
        assert len(calls) == expected_calls
        assert calls[-1].data["action"] == "set_direction"
        assert calls[-1].data["caller"] == _TEST_FAN
        assert calls[-1].data["option"] == cmd


async def test_set_invalid_direction(
    hass: HomeAssistant, calls: list[ServiceCall]
) -> None:
    """Test set invalid direction when fan has valid direction."""
    await _register_components(hass)

    await common.async_turn_on(hass, _TEST_FAN)
    for cmd in (DIRECTION_FORWARD, "invalid"):
        await common.async_set_direction(hass, _TEST_FAN, cmd)
        assert hass.states.get(_DIRECTION_INPUT_SELECT).state == DIRECTION_FORWARD
        _verify(hass, STATE_ON, 0, None, DIRECTION_FORWARD, None)


async def test_preset_modes(hass: HomeAssistant, calls: list[ServiceCall]) -> None:
    """Test preset_modes."""
    await _register_components(
        hass, ["off", "low", "medium", "high", "auto", "smart"], ["auto", "smart"]
    )

    await common.async_turn_on(hass, _TEST_FAN)
    for extra, state, expected_calls in (
        ("auto", "auto", 2),
        ("smart", "smart", 3),
        ("invalid", "smart", 3),
    ):
        if extra != state:
            with pytest.raises(NotValidPresetModeError):
                await common.async_set_preset_mode(hass, _TEST_FAN, extra)
        else:
            await common.async_set_preset_mode(hass, _TEST_FAN, extra)
        assert hass.states.get(_PRESET_MODE_INPUT_SELECT).state == state
        assert len(calls) == expected_calls
        assert calls[-1].data["action"] == "set_preset_mode"
        assert calls[-1].data["caller"] == _TEST_FAN
        assert calls[-1].data["option"] == state

    await common.async_turn_on(hass, _TEST_FAN, preset_mode="auto")
    assert hass.states.get(_PRESET_MODE_INPUT_SELECT).state == "auto"


async def test_set_percentage(hass: HomeAssistant, calls: list[ServiceCall]) -> None:
    """Test set valid speed percentage."""
    await _register_components(hass)
    expected_calls = 0

    await common.async_turn_on(hass, _TEST_FAN)
    expected_calls += 1
    for state, value in (
        (STATE_ON, 100),
        (STATE_ON, 66),
        (STATE_ON, 0),
    ):
        await common.async_set_percentage(hass, _TEST_FAN, value)
        assert int(float(hass.states.get(_PERCENTAGE_INPUT_NUMBER).state)) == value
        _verify(hass, state, value, None, None, None)
        expected_calls += 1
        assert len(calls) == expected_calls
        assert calls[-1].data["action"] == "set_value"
        assert calls[-1].data["caller"] == _TEST_FAN
        assert calls[-1].data["value"] == value

    await common.async_turn_on(hass, _TEST_FAN, percentage=50)
    assert int(float(hass.states.get(_PERCENTAGE_INPUT_NUMBER).state)) == 50
    _verify(hass, STATE_ON, 50, None, None, None)


async def test_increase_decrease_speed(
    hass: HomeAssistant, calls: list[ServiceCall]
) -> None:
    """Test set valid increase and decrease speed."""
    await _register_components(hass, speed_count=3)

    await common.async_turn_on(hass, _TEST_FAN)
    for func, extra, state, value in (
        (common.async_set_percentage, 100, STATE_ON, 100),
        (common.async_decrease_speed, None, STATE_ON, 66),
        (common.async_decrease_speed, None, STATE_ON, 33),
        (common.async_decrease_speed, None, STATE_ON, 0),
        (common.async_increase_speed, None, STATE_ON, 33),
    ):
        await func(hass, _TEST_FAN, extra)
        assert int(float(hass.states.get(_PERCENTAGE_INPUT_NUMBER).state)) == value
        _verify(hass, state, value, None, None, None)


async def test_optimistic_state(hass: HomeAssistant, calls: list[ServiceCall]) -> None:
    """Test a fan without a value_template."""
    await _register_fan_sources(hass)

    with assert_setup_component(1, "fan"):
        test_fan_config = {
            **OPTIMISTIC_ON_OFF_CONFIG,
            "preset_modes": ["auto"],
            **PRESET_MODE_ACTION,
            **PERCENTAGE_ACTION,
            **OSCILLATE_ACTION,
            **DIRECTION_ACTION,
        }
        assert await setup.async_setup_component(
            hass,
            "fan",
            {"fan": {"platform": "template", "fans": {"test_fan": test_fan_config}}},
        )

    await hass.async_block_till_done()
    await hass.async_start()
    await hass.async_block_till_done()

    await common.async_turn_on(hass, _TEST_FAN)
    _verify(hass, STATE_ON)

    assert len(calls) == 1
    assert calls[-1].data["action"] == "turn_on"
    assert calls[-1].data["caller"] == _TEST_FAN

    await common.async_turn_off(hass, _TEST_FAN)
    _verify(hass, STATE_OFF)

    assert len(calls) == 2
    assert calls[-1].data["action"] == "turn_off"
    assert calls[-1].data["caller"] == _TEST_FAN

    percent = 100
    await common.async_set_percentage(hass, _TEST_FAN, percent)
    _verify(hass, STATE_ON, percent)

    assert len(calls) == 3
    assert calls[-1].data["action"] == "set_percentage"
    assert calls[-1].data["percentage"] == 100
    assert calls[-1].data["caller"] == _TEST_FAN

    await common.async_turn_off(hass, _TEST_FAN)
    _verify(hass, STATE_OFF, percent)

    assert len(calls) == 4
    assert calls[-1].data["action"] == "turn_off"
    assert calls[-1].data["caller"] == _TEST_FAN

    preset = "auto"
    await common.async_set_preset_mode(hass, _TEST_FAN, preset)
    assert hass.states.get(_PRESET_MODE_INPUT_SELECT).state == preset
    _verify(hass, STATE_ON, percent, None, None, preset)

    assert len(calls) == 5
    assert calls[-1].data["action"] == "set_preset_mode"
    assert calls[-1].data["preset_mode"] == preset
    assert calls[-1].data["caller"] == _TEST_FAN

    await common.async_turn_off(hass, _TEST_FAN)
    _verify(hass, STATE_OFF, percent, None, None, preset)

    assert len(calls) == 6
    assert calls[-1].data["action"] == "turn_off"
    assert calls[-1].data["caller"] == _TEST_FAN

    await common.async_set_direction(hass, _TEST_FAN, DIRECTION_FORWARD)
    _verify(hass, STATE_OFF, percent, None, DIRECTION_FORWARD, preset)

    assert len(calls) == 7
    assert calls[-1].data["action"] == "set_direction"
    assert calls[-1].data["direction"] == DIRECTION_FORWARD
    assert calls[-1].data["caller"] == _TEST_FAN

    await common.async_oscillate(hass, _TEST_FAN, True)
    _verify(hass, STATE_OFF, percent, True, DIRECTION_FORWARD, preset)

    assert len(calls) == 8
    assert calls[-1].data["action"] == "set_oscillating"
    assert calls[-1].data["oscillating"] is True
    assert calls[-1].data["caller"] == _TEST_FAN


@pytest.mark.parametrize("count", [1])
@pytest.mark.parametrize("style", [ConfigurationStyle.LEGACY])
@pytest.mark.parametrize(
    ("extra_config", "attribute", "action", "verify_attr", "coro", "value"),
    [
        (
            OPTIMISTIC_PERCENTAGE_CONFIG,
            "percentage",
            "set_percentage",
            "expected_percentage",
            common.async_set_percentage,
            50,
        ),
        (
            OPTIMISTIC_PRESET_MODE_CONFIG2,
            "preset_mode",
            "set_preset_mode",
            "expected_preset_mode",
            common.async_set_preset_mode,
            "auto",
        ),
        (
            OPTIMISTIC_OSCILLATE_CONFIG,
            "oscillating",
            "set_oscillating",
            "expected_oscillating",
            common.async_oscillate,
            True,
        ),
        (
            OPTIMISTIC_DIRECTION_CONFIG,
            "direction",
            "set_direction",
            "expected_direction",
            common.async_set_direction,
            DIRECTION_FORWARD,
        ),
    ],
)
async def test_optimistic_attributes(
    hass: HomeAssistant,
    attribute: str,
    action: str,
    verify_attr: str,
    coro,
    value: Any,
    setup_optimistic_fan_attribute,
    calls: list[ServiceCall],
) -> None:
    """Test setting percentage with optimistic template."""

    await coro(hass, _TEST_FAN, value)
    _verify(hass, STATE_ON, **{verify_attr: value})

    assert len(calls) == 1
    assert calls[-1].data["action"] == action
    assert calls[-1].data[attribute] == value
    assert calls[-1].data["caller"] == _TEST_FAN


async def test_increase_decrease_speed_default_speed_count(
    hass: HomeAssistant, calls: list[ServiceCall]
) -> None:
    """Test set valid increase and decrease speed."""
    await _register_components(hass)

    await common.async_turn_on(hass, _TEST_FAN)
    for func, extra, state, value in (
        (common.async_set_percentage, 100, STATE_ON, 100),
        (common.async_decrease_speed, None, STATE_ON, 99),
        (common.async_decrease_speed, None, STATE_ON, 98),
        (common.async_decrease_speed, 31, STATE_ON, 67),
        (common.async_decrease_speed, None, STATE_ON, 66),
    ):
        await func(hass, _TEST_FAN, extra)
        assert int(float(hass.states.get(_PERCENTAGE_INPUT_NUMBER).state)) == value
        _verify(hass, state, value, None, None, None)


async def test_set_invalid_osc_from_initial_state(
    hass: HomeAssistant, calls: list[ServiceCall]
) -> None:
    """Test set invalid oscillating when fan is in initial state."""
    await _register_components(hass)

    await common.async_turn_on(hass, _TEST_FAN)
    with pytest.raises(vol.Invalid):
        await common.async_oscillate(hass, _TEST_FAN, "invalid")
    assert hass.states.get(_OSC_INPUT).state == ""
    _verify(hass, STATE_ON, 0, None, None, None)


async def test_set_invalid_osc(hass: HomeAssistant, calls: list[ServiceCall]) -> None:
    """Test set invalid oscillating when fan has valid osc."""
    await _register_components(hass)

    await common.async_turn_on(hass, _TEST_FAN)
    await common.async_oscillate(hass, _TEST_FAN, True)
    assert hass.states.get(_OSC_INPUT).state == "True"
    _verify(hass, STATE_ON, 0, True, None, None)

    with pytest.raises(vol.Invalid):
        await common.async_oscillate(hass, _TEST_FAN, None)
    assert hass.states.get(_OSC_INPUT).state == "True"
    _verify(hass, STATE_ON, 0, True, None, None)


def _verify(
    hass: HomeAssistant,
    expected_state: str,
    expected_percentage: int | None = None,
    expected_oscillating: bool | None = None,
    expected_direction: str | None = None,
    expected_preset_mode: str | None = None,
) -> None:
    """Verify fan's state, speed and osc."""
    state = hass.states.get(_TEST_FAN)
    attributes = state.attributes
    assert state.state == str(expected_state)
    assert attributes.get(ATTR_PERCENTAGE) == expected_percentage
    assert attributes.get(ATTR_OSCILLATING) == expected_oscillating
    assert attributes.get(ATTR_DIRECTION) == expected_direction
    assert attributes.get(ATTR_PRESET_MODE) == expected_preset_mode


async def _register_fan_sources(hass: HomeAssistant) -> None:
    with assert_setup_component(1, "input_boolean"):
        assert await setup.async_setup_component(
            hass, "input_boolean", {"input_boolean": {"state": None}}
        )

    with assert_setup_component(1, "input_number"):
        assert await setup.async_setup_component(
            hass,
            "input_number",
            {
                "input_number": {
                    "percentage": {
                        "min": 0.0,
                        "max": 100.0,
                        "name": "Percentage",
                        "step": 1.0,
                        "mode": "slider",
                    }
                }
            },
        )

    with assert_setup_component(3, "input_select"):
        assert await setup.async_setup_component(
            hass,
            "input_select",
            {
                "input_select": {
                    "preset_mode": {
                        "name": "Preset Mode",
                        "options": ["auto", "smart"],
                    },
                    "osc": {"name": "oscillating", "options": ["", "True", "False"]},
                    "direction": {
                        "name": "Direction",
                        "options": ["", DIRECTION_FORWARD, DIRECTION_REVERSE],
                    },
                }
            },
        )


async def _register_components(
    hass: HomeAssistant,
    speed_list: list[str] | None = None,
    preset_modes: list[str] | None = None,
    speed_count: int | None = None,
) -> None:
    """Register basic components for testing."""
    await _register_fan_sources(hass)

    with assert_setup_component(1, "fan"):
        value_template = """
        {% if is_state('input_boolean.state', 'on') %}
            {{ 'on' }}
        {% else %}
            {{ 'off' }}
        {% endif %}
        """

        test_fan_config = {
            "value_template": value_template,
            "preset_mode_template": "{{ states('input_select.preset_mode') }}",
            "percentage_template": "{{ states('input_number.percentage') }}",
            "oscillating_template": "{{ states('input_select.osc') }}",
            "direction_template": "{{ states('input_select.direction') }}",
            "turn_on": [
                {
                    "service": "input_boolean.turn_on",
                    "entity_id": _STATE_INPUT_BOOLEAN,
                },
                {
                    "service": "test.automation",
                    "data_template": {
                        "action": "turn_on",
                        "caller": "{{ this.entity_id }}",
                    },
                },
            ],
            "turn_off": [
                {
                    "service": "input_boolean.turn_off",
                    "entity_id": _STATE_INPUT_BOOLEAN,
                },
                {
                    "service": "input_number.set_value",
                    "data_template": {
                        "entity_id": _PERCENTAGE_INPUT_NUMBER,
                        "value": 0,
                    },
                },
                {
                    "service": "test.automation",
                    "data_template": {
                        "action": "turn_off",
                        "caller": "{{ this.entity_id }}",
                    },
                },
            ],
            "set_preset_mode": [
                {
                    "service": "input_select.select_option",
                    "data_template": {
                        "entity_id": _PRESET_MODE_INPUT_SELECT,
                        "option": "{{ preset_mode }}",
                    },
                },
                {
                    "service": "test.automation",
                    "data_template": {
                        "action": "set_preset_mode",
                        "caller": "{{ this.entity_id }}",
                        "option": "{{ preset_mode }}",
                    },
                },
            ],
            "set_percentage": [
                {
                    "service": "input_number.set_value",
                    "data_template": {
                        "entity_id": _PERCENTAGE_INPUT_NUMBER,
                        "value": "{{ percentage }}",
                    },
                },
                {
                    "service": "test.automation",
                    "data_template": {
                        "action": "set_value",
                        "caller": "{{ this.entity_id }}",
                        "value": "{{ percentage }}",
                    },
                },
            ],
            "set_oscillating": [
                {
                    "service": "input_select.select_option",
                    "data_template": {
                        "entity_id": _OSC_INPUT,
                        "option": "{{ oscillating }}",
                    },
                },
                {
                    "service": "test.automation",
                    "data_template": {
                        "action": "set_oscillating",
                        "caller": "{{ this.entity_id }}",
                        "option": "{{ oscillating }}",
                    },
                },
            ],
            "set_direction": [
                {
                    "service": "input_select.select_option",
                    "data_template": {
                        "entity_id": _DIRECTION_INPUT_SELECT,
                        "option": "{{ direction }}",
                    },
                },
                {
                    "service": "test.automation",
                    "data_template": {
                        "action": "set_direction",
                        "caller": "{{ this.entity_id }}",
                        "option": "{{ direction }}",
                    },
                },
            ],
        }

        if preset_modes:
            test_fan_config["preset_modes"] = preset_modes

        if speed_count:
            test_fan_config["speed_count"] = speed_count

        assert await setup.async_setup_component(
            hass,
            "fan",
            {"fan": {"platform": "template", "fans": {"test_fan": test_fan_config}}},
        )

    await hass.async_block_till_done()
    await hass.async_start()
    await hass.async_block_till_done()


@pytest.mark.parametrize(("count", "domain"), [(1, FAN_DOMAIN)])
@pytest.mark.parametrize(
    "config",
    [
        {
            FAN_DOMAIN: {
                "platform": "template",
                "fans": {
                    "test_template_fan_01": {
                        "unique_id": "not-so-unique-anymore",
                        "value_template": "{{ true }}",
                        "turn_on": {
                            "service": "fan.turn_on",
                            "entity_id": "fan.test_state",
                        },
                        "turn_off": {
                            "service": "fan.turn_off",
                            "entity_id": "fan.test_state",
                        },
                    },
                    "test_template_fan_02": {
                        "unique_id": "not-so-unique-anymore",
                        "value_template": "{{ false }}",
                        "turn_on": {
                            "service": "fan.turn_on",
                            "entity_id": "fan.test_state",
                        },
                        "turn_off": {
                            "service": "fan.turn_off",
                            "entity_id": "fan.test_state",
                        },
                    },
                },
            }
        },
    ],
)
@pytest.mark.usefixtures("start_ha")
async def test_unique_id(hass: HomeAssistant) -> None:
    """Test unique_id option only creates one fan per id."""
    assert len(hass.states.async_all()) == 1


@pytest.mark.parametrize(
    ("speed_count", "percentage_step"), [(0, 1), (100, 1), (3, 100 / 3)]
)
async def test_implemented_percentage(
    hass: HomeAssistant, speed_count, percentage_step
) -> None:
    """Test a fan that implements percentage."""
    await setup.async_setup_component(
        hass,
        "fan",
        {
            "fan": {
                "platform": "template",
                "fans": {
                    "mechanical_ventilation": {
                        "friendly_name": "Mechanische ventilatie",
                        "unique_id": "a2fd2e38-674b-4b47-b5ef-cc2362211a72",
                        "value_template": "{{ states('light.mv_snelheid') }}",
                        "percentage_template": (
                            "{{ (state_attr('light.mv_snelheid','brightness') | int /"
                            " 255 * 100) | int }}"
                        ),
                        "turn_on": [
                            {
                                "service": "switch.turn_off",
                                "target": {
                                    "entity_id": "switch.mv_automatisch",
                                },
                            },
                            {
                                "service": "light.turn_on",
                                "target": {
                                    "entity_id": "light.mv_snelheid",
                                },
                                "data": {"brightness_pct": 40},
                            },
                        ],
                        "turn_off": [
                            {
                                "service": "light.turn_off",
                                "target": {
                                    "entity_id": "light.mv_snelheid",
                                },
                            },
                            {
                                "service": "switch.turn_on",
                                "target": {
                                    "entity_id": "switch.mv_automatisch",
                                },
                            },
                        ],
                        "set_percentage": [
                            {
                                "service": "light.turn_on",
                                "target": {
                                    "entity_id": "light.mv_snelheid",
                                },
                                "data": {"brightness_pct": "{{ percentage }}"},
                            }
                        ],
                        "speed_count": speed_count,
                    },
                },
            },
        },
    )

    await hass.async_block_till_done()
    await hass.async_start()
    await hass.async_block_till_done()

    assert len(hass.states.async_all()) == 1

    state = hass.states.get("fan.mechanical_ventilation")
    attributes = state.attributes
    assert attributes["percentage_step"] == percentage_step
    assert attributes.get("supported_features") & FanEntityFeature.SET_SPEED


@pytest.mark.parametrize(("count", "domain"), [(1, FAN_DOMAIN)])
@pytest.mark.parametrize(
    "config",
    [
        {
            FAN_DOMAIN: {
                "platform": "template",
                "fans": {
                    "mechanical_ventilation": {
                        "friendly_name": "Mechanische ventilatie",
                        "unique_id": "a2fd2e38-674b-4b47-b5ef-cc2362211a72",
                        "value_template": "{{ states('light.mv_snelheid') }}",
                        "preset_mode_template": "{{ 'any' }}",
                        "preset_modes": ["any"],
                        "set_preset_mode": [
                            {
                                "service": "light.turn_on",
                                "target": {
                                    "entity_id": "light.mv_snelheid",
                                },
                                "data": {"brightness_pct": "{{ percentage }}"},
                            }
                        ],
                        "turn_on": [
                            {
                                "service": "switch.turn_off",
                                "target": {
                                    "entity_id": "switch.mv_automatisch",
                                },
                            },
                            {
                                "service": "light.turn_on",
                                "target": {
                                    "entity_id": "light.mv_snelheid",
                                },
                                "data": {"brightness_pct": 40},
                            },
                        ],
                        "turn_off": [
                            {
                                "service": "light.turn_off",
                                "target": {
                                    "entity_id": "light.mv_snelheid",
                                },
                            },
                            {
                                "service": "switch.turn_on",
                                "target": {
                                    "entity_id": "switch.mv_automatisch",
                                },
                            },
                        ],
                    },
                },
            }
        },
    ],
)
@pytest.mark.usefixtures("start_ha")
async def test_implemented_preset_mode(hass: HomeAssistant) -> None:
    """Test a fan that implements preset_mode."""
    assert len(hass.states.async_all()) == 1

    state = hass.states.get("fan.mechanical_ventilation")
    attributes = state.attributes
    assert attributes.get("percentage") is None
    assert attributes.get("supported_features") & FanEntityFeature.PRESET_MODE


@pytest.mark.parametrize("count", [1])
@pytest.mark.parametrize(
    ("style", "fan_config"),
    [
        (
            ConfigurationStyle.LEGACY,
            {
                "turn_on": [],
                "turn_off": [],
            },
        ),
    ],
)
@pytest.mark.parametrize(
    ("extra_config", "supported_features"),
    [
        (
            {
                "set_percentage": [],
            },
            FanEntityFeature.SET_SPEED,
        ),
        (
            {
                "set_preset_mode": [],
            },
            FanEntityFeature.PRESET_MODE,
        ),
        (
            {
                "set_oscillating": [],
            },
            FanEntityFeature.OSCILLATE,
        ),
        (
            {
                "set_direction": [],
            },
            FanEntityFeature.DIRECTION,
        ),
    ],
)
async def test_empty_action_config(
    hass: HomeAssistant,
    supported_features: FanEntityFeature,
    setup_test_fan_with_extra_config,
) -> None:
    """Test configuration with empty script."""
    state = hass.states.get(_TEST_FAN)
    assert state.attributes["supported_features"] == (
        FanEntityFeature.TURN_OFF | FanEntityFeature.TURN_ON | supported_features
    )
