"""Fixtures for HomeWizard integration tests."""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

from homewizard_energy.models import (
    Batteries,
    CombinedModels,
    Device,
    Measurement,
    State,
    System,
    Token,
)
import pytest

from homeassistant.components.homewizard.const import DOMAIN
from homeassistant.const import CONF_IP_ADDRESS, CONF_TOKEN
from homeassistant.core import HomeAssistant

from tests.common import MockConfigEntry, get_fixture_path, load_json_object_fixture


@pytest.fixture
def device_fixture() -> str:
    """Return the device fixtures for a specific device."""
    return "HWE-P1"


@pytest.fixture
def mock_homewizardenergy(
    device_fixture: str,
) -> MagicMock:
    """Return a mock bridge."""
    with (
        patch(
            "homeassistant.components.homewizard.HomeWizardEnergyV1",
            autospec=True,
        ) as homewizard,
        patch(
            "homeassistant.components.homewizard.config_flow.HomeWizardEnergyV1",
            new=homewizard,
        ),
    ):
        client = homewizard.return_value

        client.combined.return_value = CombinedModels(
            device=Device.from_dict(
                load_json_object_fixture(f"{device_fixture}/device.json", DOMAIN)
            ),
            measurement=Measurement.from_dict(
                load_json_object_fixture(f"{device_fixture}/data.json", DOMAIN)
            ),
            state=(
                State.from_dict(
                    load_json_object_fixture(f"{device_fixture}/state.json", DOMAIN)
                )
                if get_fixture_path(f"{device_fixture}/state.json", DOMAIN).exists()
                else None
            ),
            system=(
                System.from_dict(
                    load_json_object_fixture(f"{device_fixture}/system.json", DOMAIN)
                )
                if get_fixture_path(f"{device_fixture}/system.json", DOMAIN).exists()
                else None
            ),
            batteries=(
                Batteries.from_dict(
                    load_json_object_fixture(f"{device_fixture}/batteries.json", DOMAIN)
                )
                if get_fixture_path(f"{device_fixture}/batteries.json", DOMAIN).exists()
                else None
            ),
        )

        # device() call is used during configuration flow
        client.device.return_value = client.combined.return_value.device

        yield client


@pytest.fixture
def mock_homewizardenergy_v2(
    device_fixture: str,
) -> MagicMock:
    """Return a mock bridge."""
    with (
        patch(
            "homeassistant.components.homewizard.HomeWizardEnergyV2",
            autospec=True,
        ) as homewizard,
        patch(
            "homeassistant.components.homewizard.config_flow.HomeWizardEnergyV2",
            new=homewizard,
        ),
    ):
        client = homewizard.return_value

        client.combined.return_value = CombinedModels(
            device=Device.from_dict(
                load_json_object_fixture(f"v2/{device_fixture}/device.json", DOMAIN)
            ),
            measurement=Measurement.from_dict(
                load_json_object_fixture(
                    f"v2/{device_fixture}/measurement.json", DOMAIN
                )
            ),
            state=(
                State.from_dict(
                    load_json_object_fixture(f"v2/{device_fixture}/state.json", DOMAIN)
                )
                if get_fixture_path(f"v2/{device_fixture}/state.json", DOMAIN).exists()
                else None
            ),
            system=(
                System.from_dict(
                    load_json_object_fixture(f"v2/{device_fixture}/system.json", DOMAIN)
                )
                if get_fixture_path(f"v2/{device_fixture}/system.json", DOMAIN).exists()
                else None
            ),
            batteries=(
                Batteries.from_dict(
                    load_json_object_fixture(f"{device_fixture}/batteries.json", DOMAIN)
                )
                if get_fixture_path(f"{device_fixture}/batteries.json", DOMAIN).exists()
                else None
            ),
        )

        # device() call is used during configuration flow
        client.device.return_value = client.combined.return_value.device

        # Authorization flow is used during configuration flow
        client.get_token.return_value = Token.from_dict(
            load_json_object_fixture("v2/generic/token.json", DOMAIN)
        ).token

        yield client


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Mock setting up a config entry."""
    with patch(
        "homeassistant.components.homewizard.async_setup_entry", return_value=True
    ) as mock_setup:
        yield mock_setup


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return the default mocked config entry."""
    return MockConfigEntry(
        title="Device",
        domain=DOMAIN,
        data={
            "product_name": "P1 Meter",
            "product_type": "HWE-P1",
            "serial": "5c2fafabcdef",
            CONF_IP_ADDRESS: "127.0.0.1",
        },
        unique_id="HWE-P1_5c2fafabcdef",
    )


@pytest.fixture
def mock_config_entry_v2() -> MockConfigEntry:
    """Return the default mocked config entry."""
    return MockConfigEntry(
        title="Device",
        domain=DOMAIN,
        data={
            CONF_IP_ADDRESS: "127.0.0.1",
            CONF_TOKEN: "00112233445566778899ABCDEFABCDEF",
        },
        unique_id="HWE-BAT_5c2fafabcdef",
    )


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_homewizardenergy: AsyncMock,
) -> MockConfigEntry:
    """Set up the HomeWizard integration for testing."""
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    return mock_config_entry


@pytest.fixture
def mock_onboarding() -> Generator[MagicMock]:
    """Mock that Home Assistant is currently onboarding."""
    with patch(
        "homeassistant.components.onboarding.async_is_onboarded",
        return_value=False,
    ) as mock_onboarding:
        yield mock_onboarding
