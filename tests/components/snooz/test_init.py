"""Test Snooz configuration."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from . import SnoozFixture


async def test_removing_entry_cleans_up_connections(
    hass: HomeAssistant, mock_connected_snooz: SnoozFixture
) -> None:
    """Tests setup and removal of a config entry, ensuring connections are cleaned up."""
    await hass.config_entries.async_remove(mock_connected_snooz.entry.entry_id)
    await hass.async_block_till_done()

    assert not mock_connected_snooz.device.is_connected


async def test_reloading_entry_cleans_up_connections(
    hass: HomeAssistant, mock_connected_snooz: SnoozFixture
) -> None:
    """Test reloading an entry disconnects any existing connections."""
    await hass.config_entries.async_reload(mock_connected_snooz.entry.entry_id)
    await hass.async_block_till_done()

    assert not mock_connected_snooz.device.is_connected
