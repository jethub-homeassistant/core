"""ViCare helpers functions."""

import logging

from PyViCare.PyViCareDevice import Device as PyViCareDevice
from PyViCare.PyViCareDeviceConfig import PyViCareDeviceConfig
from PyViCare.PyViCareHeatingDevice import (
    HeatingDeviceWithComponent as PyViCareHeatingDeviceComponent,
)
from PyViCare.PyViCareUtils import (
    PyViCareInvalidDataError,
    PyViCareNotSupportedFeatureError,
    PyViCareRateLimitError,
)
import requests

from homeassistant.config_entries import ConfigEntry

from .const import CONF_HEATING_TYPE, HEATING_TYPE_TO_CREATOR_METHOD, HeatingType
from .types import ViCareRequiredKeysMixin

_LOGGER = logging.getLogger(__name__)


def get_device(
    entry: ConfigEntry, device_config: PyViCareDeviceConfig
) -> PyViCareDevice:
    """Get device for device config."""
    return getattr(
        device_config,
        HEATING_TYPE_TO_CREATOR_METHOD[HeatingType(entry.data[CONF_HEATING_TYPE])],
    )()


def get_device_serial(device: PyViCareDevice) -> str | None:
    """Get device serial for device if supported."""
    try:
        return device.getSerial()
    except PyViCareNotSupportedFeatureError:
        _LOGGER.debug("Device does not offer a 'device.serial' data point")
    except PyViCareRateLimitError as limit_exception:
        _LOGGER.debug("Vicare API rate limit exceeded: %s", limit_exception)
    except PyViCareInvalidDataError as invalid_data_exception:
        _LOGGER.debug("Invalid data from Vicare server: %s", invalid_data_exception)
    except requests.exceptions.ConnectionError:
        _LOGGER.debug("Unable to retrieve data from ViCare server")
    except ValueError:
        _LOGGER.debug("Unable to decode data from ViCare server")
    return None


def is_supported(
    name: str,
    entity_description: ViCareRequiredKeysMixin,
    vicare_device,
) -> bool:
    """Check if the PyViCare device supports the requested sensor."""
    try:
        entity_description.value_getter(vicare_device)
    except PyViCareNotSupportedFeatureError:
        _LOGGER.debug("Feature not supported %s", name)
        return False
    except AttributeError as error:
        _LOGGER.debug("Feature not supported %s: %s", name, error)
        return False
    _LOGGER.debug("Found entity %s", name)
    return True


def get_burners(device: PyViCareDevice) -> list[PyViCareHeatingDeviceComponent]:
    """Return the list of burners."""
    try:
        return device.burners
    except PyViCareNotSupportedFeatureError:
        _LOGGER.debug("No burners found")
    except AttributeError as error:
        _LOGGER.debug("No burners found: %s", error)
    return []


def get_circuits(device: PyViCareDevice) -> list[PyViCareHeatingDeviceComponent]:
    """Return the list of circuits."""
    try:
        return device.circuits
    except PyViCareNotSupportedFeatureError:
        _LOGGER.debug("No circuits found")
    except AttributeError as error:
        _LOGGER.debug("No circuits found: %s", error)
    return []


def get_compressors(device: PyViCareDevice) -> list[PyViCareHeatingDeviceComponent]:
    """Return the list of compressors."""
    try:
        return device.compressors
    except PyViCareNotSupportedFeatureError:
        _LOGGER.debug("No compressors found")
    except AttributeError as error:
        _LOGGER.debug("No compressors found: %s", error)
    return []
