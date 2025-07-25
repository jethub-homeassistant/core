"""Test Tuya number platform."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from syrupy.assertion import SnapshotAssertion
from tuya_sharing import CustomerDevice

from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN, SERVICE_SET_VALUE
from homeassistant.components.tuya import ManagerCompat
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from . import DEVICE_MOCKS, initialize_entry

from tests.common import MockConfigEntry, snapshot_platform


@pytest.mark.parametrize(
    "mock_device_code", [k for k, v in DEVICE_MOCKS.items() if Platform.NUMBER in v]
)
@patch("homeassistant.components.tuya.PLATFORMS", [Platform.NUMBER])
async def test_platform_setup_and_discovery(
    hass: HomeAssistant,
    mock_manager: ManagerCompat,
    mock_config_entry: MockConfigEntry,
    mock_device: CustomerDevice,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Test platform setup and discovery."""
    await initialize_entry(hass, mock_manager, mock_config_entry, mock_device)

    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.parametrize(
    "mock_device_code", [k for k, v in DEVICE_MOCKS.items() if Platform.NUMBER not in v]
)
@patch("homeassistant.components.tuya.PLATFORMS", [Platform.NUMBER])
async def test_platform_setup_no_discovery(
    hass: HomeAssistant,
    mock_manager: ManagerCompat,
    mock_config_entry: MockConfigEntry,
    mock_device: CustomerDevice,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test platform setup without discovery."""
    await initialize_entry(hass, mock_manager, mock_config_entry, mock_device)

    assert not er.async_entries_for_config_entry(
        entity_registry, mock_config_entry.entry_id
    )


@pytest.mark.parametrize(
    "mock_device_code",
    ["mal_alarm_host"],
)
async def test_set_value(
    hass: HomeAssistant,
    mock_manager: ManagerCompat,
    mock_config_entry: MockConfigEntry,
    mock_device: CustomerDevice,
) -> None:
    """Test set value."""
    entity_id = "number.multifunction_alarm_arm_delay"
    await initialize_entry(hass, mock_manager, mock_config_entry, mock_device)

    state = hass.states.get(entity_id)
    assert state is not None, f"{entity_id} does not exist"
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {
            "entity_id": entity_id,
            "value": 18,
        },
    )
    await hass.async_block_till_done()
    mock_manager.send_commands.assert_called_once_with(
        mock_device.id, [{"code": "delay_set", "value": 18}]
    )


@pytest.mark.parametrize(
    "mock_device_code",
    ["mal_alarm_host"],
)
async def test_set_value_no_function(
    hass: HomeAssistant,
    mock_manager: ManagerCompat,
    mock_config_entry: MockConfigEntry,
    mock_device: CustomerDevice,
) -> None:
    """Test set value when no function available."""

    # Mock a device with delay_set in status but not in function or status_range
    mock_device.function.pop("delay_set")
    mock_device.status_range.pop("delay_set")

    entity_id = "number.multifunction_alarm_arm_delay"
    await initialize_entry(hass, mock_manager, mock_config_entry, mock_device)

    state = hass.states.get(entity_id)
    assert state is not None, f"{entity_id} does not exist"
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {
                "entity_id": entity_id,
                "value": 18,
            },
            blocking=True,
        )
    assert err.value.translation_key == "action_dpcode_not_found"
    assert err.value.translation_placeholders == {
        "expected": "['delay_set']",
        "available": (
            "['alarm_delay_time', 'alarm_time', 'master_mode', 'master_state', "
            "'muffling', 'sub_admin', 'sub_class', 'switch_alarm_light', "
            "'switch_alarm_propel', 'switch_alarm_sound', 'switch_kb_light', "
            "'switch_kb_sound', 'switch_mode_sound']"
        ),
    }
