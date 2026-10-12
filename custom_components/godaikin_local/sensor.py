"""Diagnostic sensors.  Only created for fields the unit actually reports (firmware V1.2.3 reports none)."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DaikinConfigEntry
from .const import CONF_THING_NAME
from .coordinator import DaikinLocalCoordinator
from .entity import DaikinLocalEntity
from .protocol import F_ERROR, F_HUMIDITY, F_OUTDOOR_TEMP, F_POWER, F_ROOM_TEMP, F_VERSION, to_float

# Coordinator-based platform: entity updates are serialised by the coordinator.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class DaikinSensorDescription(SensorEntityDescription):
    field: str
    numeric: bool = True


SENSORS: tuple[DaikinSensorDescription, ...] = (
    DaikinSensorDescription(
        key="room_temperature",
        field=F_ROOM_TEMP,
        translation_key="room_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    DaikinSensorDescription(
        key="outdoor_temperature",
        field=F_OUTDOOR_TEMP,
        translation_key="outdoor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    DaikinSensorDescription(
        key="humidity",
        field=F_HUMIDITY,
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    ),
    DaikinSensorDescription(
        key="power",
        field=F_POWER,
        translation_key="power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    DaikinSensorDescription(
        key="error_code",
        field=F_ERROR,
        translation_key="error_code",
        numeric=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DaikinSensorDescription(
        key="firmware_version",
        field=F_VERSION,
        translation_key="firmware_version",
        numeric=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: DaikinConfigEntry, add: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    present = coordinator.data or {}
    add(
        DaikinLocalSensor(coordinator, description, entry.data[CONF_THING_NAME])
        for description in SENSORS
        if description.field in present
    )


class DaikinLocalSensor(DaikinLocalEntity, SensorEntity):
    entity_description: DaikinSensorDescription

    def __init__(
        self, coordinator: DaikinLocalCoordinator, description: DaikinSensorDescription, thing_name: str
    ) -> None:
        super().__init__(coordinator, thing_name, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | None:
        raw = self.state_doc.get(self.entity_description.field)
        if raw is None:
            return None
        return to_float(raw) if self.entity_description.numeric else str(raw)
