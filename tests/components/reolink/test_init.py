"""Test the Reolink init."""

import asyncio
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from reolink_aio.exceptions import (
    CredentialsInvalidError,
    LoginPrivacyModeError,
    ReolinkError,
)

from homeassistant.components.reolink import (
    DEVICE_UPDATE_INTERVAL,
    FIRMWARE_UPDATE_INTERVAL,
    NUM_CRED_ERRORS,
)
from homeassistant.components.reolink.const import (
    BATTERY_ALL_WAKE_UPDATE_INTERVAL,
    BATTERY_PASSIVE_WAKE_UPDATE_INTERVAL,
    CONF_BC_PORT,
    DOMAIN,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_PROTOCOL,
    CONF_USERNAME,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    Platform,
)
from homeassistant.core import DOMAIN as HOMEASSISTANT_DOMAIN, HomeAssistant
from homeassistant.core_config import async_process_ha_core_config
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, format_mac
from homeassistant.setup import async_setup_component

from .conftest import (
    CONF_SUPPORTS_PRIVACY_MODE,
    CONF_USE_HTTPS,
    DEFAULT_PROTOCOL,
    TEST_BC_PORT,
    TEST_CAM_MODEL,
    TEST_HOST,
    TEST_HOST_MODEL,
    TEST_MAC,
    TEST_MAC_CAM,
    TEST_NVR_NAME,
    TEST_PORT,
    TEST_PRIVACY,
    TEST_UID,
    TEST_UID_CAM,
    TEST_USE_HTTPS,
    TEST_USERNAME,
)

from tests.common import MockConfigEntry, async_fire_time_changed
from tests.typing import WebSocketGenerator

pytestmark = pytest.mark.usefixtures("reolink_host", "reolink_platforms")

CHIME_MODEL = "Reolink Chime"


async def test_wait(*args, **key_args) -> None:
    """Ensure a mocked function takes a bit of time to be able to timeout in test."""
    await asyncio.sleep(0)


@pytest.mark.parametrize(
    ("attr", "value", "expected"),
    [
        (
            "is_admin",
            False,
            ConfigEntryState.SETUP_ERROR,
        ),
        (
            "get_host_data",
            AsyncMock(side_effect=ReolinkError("Test error")),
            ConfigEntryState.SETUP_RETRY,
        ),
        (
            "get_host_data",
            AsyncMock(side_effect=ValueError("Test error")),
            ConfigEntryState.SETUP_ERROR,
        ),
        (
            "get_states",
            AsyncMock(side_effect=ReolinkError("Test error")),
            ConfigEntryState.SETUP_RETRY,
        ),
        (
            "get_host_data",
            AsyncMock(side_effect=CredentialsInvalidError("Test error")),
            ConfigEntryState.SETUP_ERROR,
        ),
        (
            "supported",
            Mock(return_value=False),
            ConfigEntryState.LOADED,
        ),
    ],
)
async def test_failures_parametrized(
    hass: HomeAssistant,
    reolink_host: MagicMock,
    config_entry: MockConfigEntry,
    attr: str,
    value: Any,
    expected: ConfigEntryState,
) -> None:
    """Test outcomes when changing errors."""
    setattr(reolink_host, attr, value)
    assert await hass.config_entries.async_setup(config_entry.entry_id) is (
        expected is ConfigEntryState.LOADED
    )
    await hass.async_block_till_done()

    assert config_entry.state == expected


async def test_firmware_error_twice(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    reolink_host: MagicMock,
    config_entry: MockConfigEntry,
) -> None:
    """Test when the firmware update fails 2 times."""
    reolink_host.check_new_firmware.side_effect = ReolinkError("Test error")
    with patch("homeassistant.components.reolink.PLATFORMS", [Platform.UPDATE]):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED

    entity_id = f"{Platform.UPDATE}.{TEST_NVR_NAME}_firmware"
    assert hass.states.get(entity_id).state == STATE_OFF

    freezer.tick(FIRMWARE_UPDATE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE


async def test_credential_error_three(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    reolink_host: MagicMock,
    config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test when the update gives credential error 3 times."""
    with patch("homeassistant.components.reolink.PLATFORMS", [Platform.SWITCH]):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED

    reolink_host.get_states.side_effect = CredentialsInvalidError("Test error")

    issue_id = f"config_entry_reauth_{DOMAIN}_{config_entry.entry_id}"
    for _ in range(NUM_CRED_ERRORS):
        assert (HOMEASSISTANT_DOMAIN, issue_id) not in issue_registry.issues
        freezer.tick(DEVICE_UPDATE_INTERVAL)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    assert (HOMEASSISTANT_DOMAIN, issue_id) in issue_registry.issues


@pytest.mark.parametrize(
    ("attr", "value", "expected_models"),
    [
        (
            None,
            None,
            [TEST_HOST_MODEL, TEST_CAM_MODEL],
        ),
        (
            "is_nvr",
            False,
            [TEST_HOST_MODEL, TEST_CAM_MODEL],
        ),
        ("channels", [], [TEST_HOST_MODEL]),
        (
            "camera_online",
            Mock(return_value=False),
            [TEST_HOST_MODEL],
        ),
        (
            "channel_for_uid",
            Mock(return_value=-1),
            [TEST_HOST_MODEL],
        ),
    ],
)
async def test_removing_disconnected_cams(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    config_entry: MockConfigEntry,
    reolink_host: MagicMock,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    attr: str | None,
    value: Any,
    expected_models: list[str],
) -> None:
    """Test device and entity registry are cleaned up when camera is removed."""
    reolink_host.channels = [0]
    assert await async_setup_component(hass, "config", {})
    client = await hass_ws_client(hass)
    # setup CH 0 and NVR switch entities/device
    with patch("homeassistant.components.reolink.PLATFORMS", [Platform.SWITCH]):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    device_entries = dr.async_entries_for_config_entry(
        device_registry, config_entry.entry_id
    )
    device_models = [device.model for device in device_entries]
    assert sorted(device_models) == sorted([TEST_HOST_MODEL, TEST_CAM_MODEL])

    # Try to remove the device after 'disconnecting' a camera.
    if attr is not None:
        setattr(reolink_host, attr, value)
    expected_success = TEST_CAM_MODEL not in expected_models
    for device in device_entries:
        if device.model == TEST_CAM_MODEL:
            response = await client.remove_device(device.id, config_entry.entry_id)
            assert response["success"] == expected_success

    device_entries = dr.async_entries_for_config_entry(
        device_registry, config_entry.entry_id
    )
    device_models = [device.model for device in device_entries]
    assert sorted(device_models) == sorted(expected_models)


@pytest.mark.parametrize(
    ("attr", "value", "expected_models", "expected_remove_call_count"),
    [
        (
            None,
            None,
            [TEST_HOST_MODEL, TEST_CAM_MODEL, CHIME_MODEL],
            1,
        ),
        (
            "connect_state",
            -1,
            [TEST_HOST_MODEL, TEST_CAM_MODEL],
            0,
        ),
        (
            "remove",
            -1,
            [TEST_HOST_MODEL, TEST_CAM_MODEL],
            1,
        ),
    ],
)
async def test_removing_chime(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    config_entry: MockConfigEntry,
    reolink_host: MagicMock,
    reolink_chime: MagicMock,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    attr: str | None,
    value: Any,
    expected_models: list[str],
    expected_remove_call_count: int,
) -> None:
    """Test removing a chime."""
    reolink_host.channels = [0]
    assert await async_setup_component(hass, "config", {})
    client = await hass_ws_client(hass)
    # setup CH 0 and NVR switch entities/device
    with patch("homeassistant.components.reolink.PLATFORMS", [Platform.SWITCH]):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    device_entries = dr.async_entries_for_config_entry(
        device_registry, config_entry.entry_id
    )
    device_models = [device.model for device in device_entries]
    assert sorted(device_models) == sorted(
        [TEST_HOST_MODEL, TEST_CAM_MODEL, CHIME_MODEL]
    )

    if attr == "remove":

        async def test_remove_chime(*args, **key_args):
            """Remove chime."""
            reolink_chime.connect_state = -1

        reolink_chime.remove = AsyncMock(side_effect=test_remove_chime)
    elif attr is not None:
        setattr(reolink_chime, attr, value)

    # Try to remove the device after 'disconnecting' a chime.
    expected_success = CHIME_MODEL not in expected_models
    for device in device_entries:
        if device.model == CHIME_MODEL:
            response = await client.remove_device(device.id, config_entry.entry_id)
            assert response["success"] == expected_success
            assert reolink_chime.remove.call_count == expected_remove_call_count

    device_entries = dr.async_entries_for_config_entry(
        device_registry, config_entry.entry_id
    )
    device_models = [device.model for device in device_entries]
    assert sorted(device_models) == sorted(expected_models)


@pytest.mark.parametrize(
    (
        "original_id",
        "new_id",
        "original_dev_id",
        "new_dev_id",
        "domain",
        "support_uid",
        "support_ch_uid",
    ),
    [
        (
            TEST_MAC,
            f"{TEST_MAC}_firmware",
            f"{TEST_MAC}",
            f"{TEST_MAC}",
            Platform.UPDATE,
            False,
            False,
        ),
        (
            TEST_MAC,
            f"{TEST_UID}_firmware",
            f"{TEST_MAC}",
            f"{TEST_UID}",
            Platform.UPDATE,
            True,
            False,
        ),
        (
            f"{TEST_MAC}_0_record_audio",
            f"{TEST_UID}_0_record_audio",
            f"{TEST_MAC}_ch0",
            f"{TEST_UID}_ch0",
            Platform.SWITCH,
            True,
            False,
        ),
        (
            f"{TEST_MAC}_chime123456789_play_ringtone",
            f"{TEST_UID}_chime123456789_play_ringtone",
            f"{TEST_MAC}_chime123456789",
            f"{TEST_UID}_chime123456789",
            Platform.SELECT,
            True,
            False,
        ),
        (
            f"{TEST_MAC}_0_record_audio",
            f"{TEST_MAC}_{TEST_UID_CAM}_record_audio",
            f"{TEST_MAC}_ch0",
            f"{TEST_MAC}_{TEST_UID_CAM}",
            Platform.SWITCH,
            False,
            True,
        ),
        (
            f"{TEST_MAC}_0_record_audio",
            f"{TEST_UID}_{TEST_UID_CAM}_record_audio",
            f"{TEST_MAC}_ch0",
            f"{TEST_UID}_{TEST_UID_CAM}",
            Platform.SWITCH,
            True,
            True,
        ),
        (
            f"{TEST_UID}_0_record_audio",
            f"{TEST_UID}_{TEST_UID_CAM}_record_audio",
            f"{TEST_UID}_ch0",
            f"{TEST_UID}_{TEST_UID_CAM}",
            Platform.SWITCH,
            True,
            True,
        ),
        (
            f"{TEST_UID}_unexpected",
            f"{TEST_UID}_unexpected",
            f"{TEST_UID}_{TEST_UID_CAM}",
            f"{TEST_UID}_{TEST_UID_CAM}",
            Platform.SWITCH,
            True,
            True,
        ),
    ],
)
async def test_migrate_entity_ids(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    reolink_host: MagicMock,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
    original_id: str,
    new_id: str,
    original_dev_id: str,
    new_dev_id: str,
    domain: Platform,
    support_uid: bool,
    support_ch_uid: bool,
) -> None:
    """Test entity ids that need to be migrated."""

    def mock_supported(ch, capability):
        if capability == "UID" and ch is None:
            return support_uid
        if capability == "UID":
            return support_ch_uid
        return True

    reolink_host.channels = [0]
    reolink_host.supported = mock_supported

    dev_entry = device_registry.async_get_or_create(
        identifiers={(DOMAIN, original_dev_id)},
        config_entry_id=config_entry.entry_id,
        disabled_by=None,
    )

    entity_registry.async_get_or_create(
        domain=domain,
        platform=DOMAIN,
        unique_id=original_id,
        config_entry=config_entry,
        suggested_object_id=original_id,
        disabled_by=None,
        device_id=dev_entry.id,
    )

    assert entity_registry.async_get_entity_id(domain, DOMAIN, original_id)
    if original_id != new_id:
        assert entity_registry.async_get_entity_id(domain, DOMAIN, new_id) is None

    assert device_registry.async_get_device(identifiers={(DOMAIN, original_dev_id)})
    if new_dev_id != original_dev_id:
        assert (
            device_registry.async_get_device(identifiers={(DOMAIN, new_dev_id)}) is None
        )

    # setup CH 0 and host entities/device
    with patch("homeassistant.components.reolink.PLATFORMS", [domain]):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    if original_id != new_id:
        assert entity_registry.async_get_entity_id(domain, DOMAIN, original_id) is None
    assert entity_registry.async_get_entity_id(domain, DOMAIN, new_id)

    if new_dev_id != original_dev_id:
        assert (
            device_registry.async_get_device(identifiers={(DOMAIN, original_dev_id)})
            is None
        )
    assert device_registry.async_get_device(identifiers={(DOMAIN, new_dev_id)})


async def test_migrate_with_already_existing_device(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    reolink_host: MagicMock,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Test device ids that need to be migrated while the new ids already exist."""
    original_dev_id = f"{TEST_MAC}_ch0"
    new_dev_id = f"{TEST_UID}_{TEST_UID_CAM}"
    domain = Platform.SWITCH

    def mock_supported(ch, capability):
        if capability == "UID" and ch is None:
            return True
        if capability == "UID":
            return True
        return True

    reolink_host.channels = [0]
    reolink_host.supported = mock_supported

    device_registry.async_get_or_create(
        identifiers={(DOMAIN, new_dev_id)},
        config_entry_id=config_entry.entry_id,
        disabled_by=None,
    )

    device_registry.async_get_or_create(
        identifiers={(DOMAIN, original_dev_id)},
        config_entry_id=config_entry.entry_id,
        disabled_by=None,
    )

    assert device_registry.async_get_device(identifiers={(DOMAIN, original_dev_id)})
    assert device_registry.async_get_device(identifiers={(DOMAIN, new_dev_id)})

    # setup CH 0 and host entities/device
    with patch("homeassistant.components.reolink.PLATFORMS", [domain]):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert (
        device_registry.async_get_device(identifiers={(DOMAIN, original_dev_id)})
        is None
    )
    assert device_registry.async_get_device(identifiers={(DOMAIN, new_dev_id)})


async def test_migrate_with_already_existing_entity(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    reolink_host: MagicMock,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Test entity ids that need to be migrated while the new ids already exist."""
    original_id = f"{TEST_UID}_0_record_audio"
    new_id = f"{TEST_UID}_{TEST_UID_CAM}_record_audio"
    dev_id = f"{TEST_UID}_{TEST_UID_CAM}"
    domain = Platform.SWITCH

    def mock_supported(ch, capability):
        if capability == "UID" and ch is None:
            return True
        if capability == "UID":
            return True
        return True

    reolink_host.channels = [0]
    reolink_host.supported = mock_supported

    dev_entry = device_registry.async_get_or_create(
        identifiers={(DOMAIN, dev_id)},
        config_entry_id=config_entry.entry_id,
        disabled_by=None,
    )

    entity_registry.async_get_or_create(
        domain=domain,
        platform=DOMAIN,
        unique_id=new_id,
        config_entry=config_entry,
        suggested_object_id=new_id,
        disabled_by=None,
        device_id=dev_entry.id,
    )

    entity_registry.async_get_or_create(
        domain=domain,
        platform=DOMAIN,
        unique_id=original_id,
        config_entry=config_entry,
        suggested_object_id=original_id,
        disabled_by=None,
        device_id=dev_entry.id,
    )

    assert entity_registry.async_get_entity_id(domain, DOMAIN, original_id)
    assert entity_registry.async_get_entity_id(domain, DOMAIN, new_id)

    # setup CH 0 and host entities/device
    with patch("homeassistant.components.reolink.PLATFORMS", [domain]):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert entity_registry.async_get_entity_id(domain, DOMAIN, original_id) is None
    assert entity_registry.async_get_entity_id(domain, DOMAIN, new_id)


async def test_cleanup_mac_connection(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    reolink_host: MagicMock,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Test cleanup of the MAC of a IPC which was set to the MAC of the host."""
    reolink_host.channels = [0]
    reolink_host.baichuan.mac_address.return_value = None
    entity_id = f"{TEST_UID}_{TEST_UID_CAM}_record_audio"
    dev_id = f"{TEST_UID}_{TEST_UID_CAM}"
    domain = Platform.SWITCH

    dev_entry = device_registry.async_get_or_create(
        identifiers={(DOMAIN, dev_id), ("OTHER_INTEGRATION", "SOME_ID")},
        connections={(CONNECTION_NETWORK_MAC, TEST_MAC)},
        config_entry_id=config_entry.entry_id,
        disabled_by=None,
    )

    entity_registry.async_get_or_create(
        domain=domain,
        platform=DOMAIN,
        unique_id=entity_id,
        config_entry=config_entry,
        suggested_object_id=entity_id,
        disabled_by=None,
        device_id=dev_entry.id,
    )

    assert entity_registry.async_get_entity_id(domain, DOMAIN, entity_id)
    device = device_registry.async_get_device(identifiers={(DOMAIN, dev_id)})
    assert device
    assert device.connections == {(CONNECTION_NETWORK_MAC, TEST_MAC)}

    # setup CH 0 and host entities/device
    with patch("homeassistant.components.reolink.PLATFORMS", [domain]):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert entity_registry.async_get_entity_id(domain, DOMAIN, entity_id)
    device = device_registry.async_get_device(identifiers={(DOMAIN, dev_id)})
    assert device
    assert device.connections == set()


async def test_cleanup_combined_with_NVR(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    reolink_host: MagicMock,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Test cleanup of the device registry if IPC camera device was combined with the NVR device."""
    reolink_host.channels = [0]
    reolink_host.baichuan.mac_address.return_value = None
    entity_id = f"{TEST_UID}_{TEST_UID_CAM}_record_audio"
    dev_id = f"{TEST_UID}_{TEST_UID_CAM}"
    domain = Platform.SWITCH
    start_identifiers = {
        (DOMAIN, dev_id),
        (DOMAIN, TEST_UID),
        ("OTHER_INTEGRATION", "SOME_ID"),
    }

    dev_entry = device_registry.async_get_or_create(
        identifiers=start_identifiers,
        connections={(CONNECTION_NETWORK_MAC, TEST_MAC)},
        config_entry_id=config_entry.entry_id,
        disabled_by=None,
    )

    entity_registry.async_get_or_create(
        domain=domain,
        platform=DOMAIN,
        unique_id=entity_id,
        config_entry=config_entry,
        suggested_object_id=entity_id,
        disabled_by=None,
        device_id=dev_entry.id,
    )

    assert entity_registry.async_get_entity_id(domain, DOMAIN, entity_id)
    device = device_registry.async_get_device(identifiers={(DOMAIN, dev_id)})
    assert device
    assert device.identifiers == start_identifiers

    # setup CH 0 and host entities/device
    with patch("homeassistant.components.reolink.PLATFORMS", [domain]):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert entity_registry.async_get_entity_id(domain, DOMAIN, entity_id)
    device = device_registry.async_get_device(identifiers={(DOMAIN, dev_id)})
    assert device
    assert device.identifiers == {(DOMAIN, dev_id)}
    host_device = device_registry.async_get_device(identifiers={(DOMAIN, TEST_UID)})
    assert host_device
    assert host_device.identifiers == {
        (DOMAIN, TEST_UID),
        ("OTHER_INTEGRATION", "SOME_ID"),
    }


async def test_cleanup_hub_and_direct_connection(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    reolink_host: MagicMock,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Test cleanup of the device registry if IPC camera device was connected directly and through the hub/NVR."""
    reolink_host.channels = [0]
    entity_id = f"{TEST_UID}_{TEST_UID_CAM}_record_audio"
    dev_id = f"{TEST_UID}_{TEST_UID_CAM}"
    domain = Platform.SWITCH
    start_identifiers = {
        (DOMAIN, dev_id),  # IPC camera through hub
        (DOMAIN, TEST_UID_CAM),  # directly connected IPC camera
        ("OTHER_INTEGRATION", "SOME_ID"),
    }

    dev_entry = device_registry.async_get_or_create(
        identifiers=start_identifiers,
        connections={(CONNECTION_NETWORK_MAC, TEST_MAC_CAM)},
        config_entry_id=config_entry.entry_id,
        disabled_by=None,
    )

    entity_registry.async_get_or_create(
        domain=domain,
        platform=DOMAIN,
        unique_id=entity_id,
        config_entry=config_entry,
        suggested_object_id=entity_id,
        disabled_by=None,
        device_id=dev_entry.id,
    )

    assert entity_registry.async_get_entity_id(domain, DOMAIN, entity_id)
    device = device_registry.async_get_device(identifiers={(DOMAIN, dev_id)})
    assert device
    assert device.identifiers == start_identifiers

    # setup CH 0 and host entities/device
    with patch("homeassistant.components.reolink.PLATFORMS", [domain]):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert entity_registry.async_get_entity_id(domain, DOMAIN, entity_id)
    device = device_registry.async_get_device(identifiers={(DOMAIN, dev_id)})
    assert device
    assert device.identifiers == start_identifiers


async def test_no_repair_issue(
    hass: HomeAssistant, config_entry: MockConfigEntry, issue_registry: ir.IssueRegistry
) -> None:
    """Test no repairs issue is raised when http local url is used."""
    await async_process_ha_core_config(
        hass, {"country": "GB", "internal_url": "http://test_homeassistant_address"}
    )

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert (DOMAIN, "https_webhook") not in issue_registry.issues
    assert (DOMAIN, "webhook_url") not in issue_registry.issues
    assert (DOMAIN, "enable_port") not in issue_registry.issues
    assert (DOMAIN, "firmware_update") not in issue_registry.issues
    assert (DOMAIN, "ssl") not in issue_registry.issues


async def test_https_repair_issue(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    reolink_host: MagicMock,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test repairs issue is raised when https local url is used."""
    reolink_host.get_states = test_wait
    await async_process_ha_core_config(
        hass, {"country": "GB", "internal_url": "https://test_homeassistant_address"}
    )

    with (
        patch("homeassistant.components.reolink.host.FIRST_ONVIF_TIMEOUT", new=0),
        patch(
            "homeassistant.components.reolink.host.FIRST_ONVIF_LONG_POLL_TIMEOUT", new=0
        ),
        patch(
            "homeassistant.components.reolink.host.ReolinkHost._async_long_polling",
        ),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert (DOMAIN, "https_webhook") in issue_registry.issues


async def test_ssl_repair_issue(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    reolink_host: MagicMock,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test repairs issue is raised when global ssl certificate is used."""
    reolink_host.get_states = test_wait
    assert await async_setup_component(hass, "webhook", {})
    hass.config.api.use_ssl = True

    await async_process_ha_core_config(
        hass, {"country": "GB", "internal_url": "http://test_homeassistant_address"}
    )

    with (
        patch("homeassistant.components.reolink.host.FIRST_ONVIF_TIMEOUT", new=0),
        patch(
            "homeassistant.components.reolink.host.FIRST_ONVIF_LONG_POLL_TIMEOUT", new=0
        ),
        patch(
            "homeassistant.components.reolink.host.ReolinkHost._async_long_polling",
        ),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert (DOMAIN, "ssl") in issue_registry.issues


@pytest.mark.parametrize("protocol", ["rtsp", "rtmp"])
async def test_port_repair_issue(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    reolink_host: MagicMock,
    protocol: str,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test repairs issue is raised when auto enable of ports fails."""
    reolink_host.set_net_port.side_effect = ReolinkError("Test error")
    reolink_host.onvif_enabled = False
    reolink_host.rtsp_enabled = False
    reolink_host.rtmp_enabled = False
    reolink_host.protocol = protocol
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert (DOMAIN, "enable_port") in issue_registry.issues


async def test_webhook_repair_issue(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    reolink_host: MagicMock,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test repairs issue is raised when the webhook url is unreachable."""
    reolink_host.get_states = test_wait
    with (
        patch("homeassistant.components.reolink.host.FIRST_ONVIF_TIMEOUT", new=0),
        patch(
            "homeassistant.components.reolink.host.FIRST_ONVIF_LONG_POLL_TIMEOUT", new=0
        ),
        patch(
            "homeassistant.components.reolink.host.ReolinkHost._async_long_polling",
        ),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert (DOMAIN, "webhook_url") in issue_registry.issues


async def test_firmware_repair_issue(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    reolink_host: MagicMock,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test firmware issue is raised when too old firmware is used."""
    reolink_host.camera_sw_version_update_required.return_value = True
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert (DOMAIN, "firmware_update_host") in issue_registry.issues


async def test_password_too_long_repair_issue(
    hass: HomeAssistant,
    reolink_host: MagicMock,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test password too long issue is raised."""
    reolink_host.valid_password.return_value = False
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=format_mac(TEST_MAC),
        data={
            CONF_HOST: TEST_HOST,
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: "too_longgggggggggggggggggggggggggggggggggggggggggggggggggg",
            CONF_PORT: TEST_PORT,
            CONF_USE_HTTPS: TEST_USE_HTTPS,
            CONF_SUPPORTS_PRIVACY_MODE: TEST_PRIVACY,
        },
        options={
            CONF_PROTOCOL: DEFAULT_PROTOCOL,
        },
        title=TEST_NVR_NAME,
    )
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert (
        DOMAIN,
        f"password_too_long_{config_entry.entry_id}",
    ) in issue_registry.issues


async def test_new_device_discovered(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    reolink_host: MagicMock,
    config_entry: MockConfigEntry,
) -> None:
    """Test the entry is reloaded when a new camera or chime is detected."""
    with patch("homeassistant.components.reolink.PLATFORMS", [Platform.SWITCH]):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert reolink_host.logout.call_count == 0
    reolink_host.new_devices = True

    freezer.tick(DEVICE_UPDATE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert reolink_host.logout.call_count == 1


async def test_port_changed(
    hass: HomeAssistant,
    reolink_host: MagicMock,
    config_entry: MockConfigEntry,
) -> None:
    """Test config_entry port update when it has changed during initial login."""
    assert config_entry.data[CONF_PORT] == TEST_PORT
    reolink_host.port = 4567

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.data[CONF_PORT] == 4567


async def test_baichuan_port_changed(
    hass: HomeAssistant,
    reolink_host: MagicMock,
    config_entry: MockConfigEntry,
) -> None:
    """Test config_entry baichuan port update when it has changed during initial login."""
    assert config_entry.data[CONF_BC_PORT] == TEST_BC_PORT
    reolink_host.baichuan.port = 8901

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.data[CONF_BC_PORT] == 8901


async def test_privacy_mode_on(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    reolink_host: MagicMock,
    config_entry: MockConfigEntry,
) -> None:
    """Test successful setup even when privacy mode is turned on."""
    reolink_host.baichuan.privacy_mode.return_value = True
    reolink_host.get_states = AsyncMock(side_effect=LoginPrivacyModeError("Test error"))

    with patch("homeassistant.components.reolink.PLATFORMS", [Platform.SWITCH]):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state == ConfigEntryState.LOADED


async def test_LoginPrivacyModeError(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    reolink_host: MagicMock,
    config_entry: MockConfigEntry,
) -> None:
    """Test normal update when get_states returns a LoginPrivacyModeError."""
    reolink_host.baichuan.privacy_mode.return_value = False
    reolink_host.get_states = AsyncMock(side_effect=LoginPrivacyModeError("Test error"))

    with patch("homeassistant.components.reolink.PLATFORMS", [Platform.SWITCH]):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    reolink_host.baichuan.check_subscribe_events.reset_mock()
    assert reolink_host.baichuan.check_subscribe_events.call_count == 0

    freezer.tick(DEVICE_UPDATE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert reolink_host.baichuan.check_subscribe_events.call_count >= 1


async def test_privacy_mode_change_callback(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    config_entry: MockConfigEntry,
    reolink_host: MagicMock,
) -> None:
    """Test privacy mode changed callback."""

    class callback_mock_class:
        callback_func = None

        def register_callback(
            self, callback_id: str, callback: Callable[[], None], *args, **key_args
        ) -> None:
            if callback_id == "privacy_mode_change":
                self.callback_func = callback

    callback_mock = callback_mock_class()

    reolink_host.model = TEST_HOST_MODEL
    reolink_host.baichuan.events_active = True
    reolink_host.baichuan.subscribe_events.reset_mock(side_effect=True)
    reolink_host.baichuan.register_callback = callback_mock.register_callback
    reolink_host.baichuan.privacy_mode.return_value = True
    reolink_host.audio_record.return_value = True

    with patch("homeassistant.components.reolink.PLATFORMS", [Platform.SWITCH]):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED

    entity_id = f"{Platform.SWITCH}.{TEST_NVR_NAME}_record_audio"
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    # simulate a TCP push callback signaling a privacy mode change
    reolink_host.baichuan.privacy_mode.return_value = False
    assert callback_mock.callback_func is not None
    callback_mock.callback_func()

    # check that a coordinator update was scheduled.
    reolink_host.get_states.reset_mock()
    assert reolink_host.get_states.call_count == 0

    freezer.tick(5)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert reolink_host.get_states.call_count >= 1
    assert hass.states.get(entity_id).state == STATE_ON

    # test cleanup during unloading, first reset to privacy mode ON
    reolink_host.baichuan.privacy_mode.return_value = True
    callback_mock.callback_func()
    freezer.tick(5)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    # now fire the callback again, but unload before refresh took place
    reolink_host.baichuan.privacy_mode.return_value = False
    callback_mock.callback_func()
    await hass.async_block_till_done()

    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_camera_wake_callback(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    config_entry: MockConfigEntry,
    reolink_host: MagicMock,
) -> None:
    """Test camera wake callback."""

    class callback_mock_class:
        callback_func = None

        def register_callback(
            self, callback_id: str, callback: Callable[[], None], *args, **key_args
        ) -> None:
            if callback_id == "camera_0_wake":
                self.callback_func = callback

    callback_mock = callback_mock_class()

    reolink_host.model = TEST_HOST_MODEL
    reolink_host.baichuan.events_active = True
    reolink_host.baichuan.subscribe_events.reset_mock(side_effect=True)
    reolink_host.baichuan.register_callback = callback_mock.register_callback
    reolink_host.sleeping.return_value = True
    reolink_host.audio_record.return_value = True

    with (
        patch("homeassistant.components.reolink.PLATFORMS", [Platform.SWITCH]),
        patch(
            "homeassistant.components.reolink.host.time",
            return_value=BATTERY_ALL_WAKE_UPDATE_INTERVAL,
        ),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED

    entity_id = f"{Platform.SWITCH}.{TEST_NVR_NAME}_record_audio"
    assert hass.states.get(entity_id).state == STATE_ON

    reolink_host.sleeping.return_value = False
    reolink_host.get_states.reset_mock()
    assert reolink_host.get_states.call_count == 0

    # simulate a TCP push callback signaling the battery camera woke up
    reolink_host.audio_record.return_value = False
    assert callback_mock.callback_func is not None
    with (
        patch(
            "homeassistant.components.reolink.host.time",
            return_value=BATTERY_ALL_WAKE_UPDATE_INTERVAL
            + BATTERY_PASSIVE_WAKE_UPDATE_INTERVAL
            + 5,
        ),
        patch(
            "homeassistant.components.reolink.time",
            return_value=BATTERY_ALL_WAKE_UPDATE_INTERVAL
            + BATTERY_PASSIVE_WAKE_UPDATE_INTERVAL
            + 5,
        ),
    ):
        callback_mock.callback_func()
        await hass.async_block_till_done()

    # check that a coordinator update was scheduled.
    assert reolink_host.get_states.call_count >= 1
    assert hass.states.get(entity_id).state == STATE_OFF


async def test_baichaun_only(
    hass: HomeAssistant,
    reolink_host: MagicMock,
    config_entry: MockConfigEntry,
) -> None:
    """Test initializing a baichuan only device."""
    reolink_host.baichuan_only = True

    with patch("homeassistant.components.reolink.PLATFORMS", [Platform.SWITCH]):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_remove(
    hass: HomeAssistant,
    reolink_host: MagicMock,
    config_entry: MockConfigEntry,
) -> None:
    """Test removing of the reolink integration."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_remove(config_entry.entry_id)
