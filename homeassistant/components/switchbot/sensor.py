"""Support for SwitchBot sensors."""

from __future__ import annotations

from switchbot import HumidifierWaterLevel
from switchbot.const.air_purifier import AirQualityLevel

from homeassistant.components.bluetooth import async_last_service_info
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    CONCENTRATION_PARTS_PER_MILLION,
    LIGHT_LUX,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SwitchbotConfigEntry, SwitchbotDataUpdateCoordinator
from .entity import SwitchbotEntity

PARALLEL_UPDATES = 0

SENSOR_TYPES: dict[str, SensorEntityDescription] = {
    "rssi": SensorEntityDescription(
        key="rssi",
        translation_key="bluetooth_signal",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "wifi_rssi": SensorEntityDescription(
        key="wifi_rssi",
        translation_key="wifi_signal",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "battery": SensorEntityDescription(
        key="battery",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "co2": SensorEntityDescription(
        key="co2",
        native_unit_of_measurement=CONCENTRATION_PARTS_PER_MILLION,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.CO2,
    ),
    "lightLevel": SensorEntityDescription(
        key="lightLevel",
        translation_key="light_level",
        native_unit_of_measurement="Level",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "humidity": SensorEntityDescription(
        key="humidity",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.HUMIDITY,
    ),
    "illuminance": SensorEntityDescription(
        key="illuminance",
        native_unit_of_measurement=LIGHT_LUX,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.ILLUMINANCE,
    ),
    "temperature": SensorEntityDescription(
        key="temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.TEMPERATURE,
    ),
    "power": SensorEntityDescription(
        key="power",
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
    ),
    "current": SensorEntityDescription(
        key="current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.CURRENT,
    ),
    "voltage": SensorEntityDescription(
        key="voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.VOLTAGE,
    ),
    "aqi_level": SensorEntityDescription(
        key="aqi_level",
        translation_key="aqi_quality_level",
        device_class=SensorDeviceClass.ENUM,
        options=[member.name.lower() for member in AirQualityLevel],
    ),
    "energy": SensorEntityDescription(
        key="energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_class=SensorDeviceClass.ENERGY,
    ),
    "water_level": SensorEntityDescription(
        key="water_level",
        translation_key="water_level",
        device_class=SensorDeviceClass.ENUM,
        options=HumidifierWaterLevel.get_levels(),
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SwitchbotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Switchbot sensor based on a config entry."""
    coordinator = entry.runtime_data
    entities = [
        SwitchBotSensor(coordinator, sensor)
        for sensor in coordinator.device.parsed_data
        if sensor in SENSOR_TYPES
    ]
    entities.append(SwitchbotRSSISensor(coordinator, "rssi"))
    async_add_entities(entities)


class SwitchBotSensor(SwitchbotEntity, SensorEntity):
    """Representation of a Switchbot sensor."""

    def __init__(
        self,
        coordinator: SwitchbotDataUpdateCoordinator,
        sensor: str,
    ) -> None:
        """Initialize the Switchbot sensor."""
        super().__init__(coordinator)
        self._sensor = sensor
        self._attr_unique_id = f"{coordinator.base_unique_id}-{sensor}"
        self.entity_description = SENSOR_TYPES[sensor]

    @property
    def native_value(self) -> str | int | None:
        """Return the state of the sensor."""
        return self.parsed_data[self._sensor]


class SwitchbotRSSISensor(SwitchBotSensor):
    """Representation of a Switchbot RSSI sensor."""

    @property
    def native_value(self) -> str | int | None:
        """Return the state of the sensor."""
        # Switchbot supports both connectable and non-connectable devices
        # so we need to request the rssi value based on the connectable instead
        # of the nearest scanner since that is the RSSI that matters for controlling
        # the device.
        if service_info := async_last_service_info(
            self.hass, self._address, self.coordinator.connectable
        ):
            return service_info.rssi
        return None
