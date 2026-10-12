"""Diagnostic sensors exposed by the unit's status document."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DaikinConfigEntry
from .const import CONF_THING_NAME, DOMAIN
from .coordinator import DaikinLocalCoordinator
from .protocol import F_ERROR, F_HUMIDITY, F_OUTDOOR_TEMP, F_POWER, F_ROOM_TEMP, _to_float


@dataclass(frozen=True, kw_only=True)
class DaikinSensorDescription(SensorEntityDescription):
    field: str
    numeric: bool = True


SENSORS: tuple[DaikinSensorDescription, ...] = (
    DaikinSensorDescription(
        key="room_temperature", field=F_ROOM_TEMP, translation_key="room_temperature",
        device_class=SensorDeviceClass.TEMPERATURE, state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    DaikinSensorDescription(
        key="outdoor_temperature", field=F_OUTDOOR_TEMP, translation_key="outdoor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE, state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    DaikinSensorDescription(
        key="humidity", field=F_HUMIDITY, translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY, state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    ),
    DaikinSensorDescription(
        key="power", field=F_POWER, translation_key="power",
        device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    DaikinSensorDescription(key="error_code", field=F_ERROR, translation_key="error_code", numeric=False),
)


async def async_setup_entry(hass: HomeAssistant, entry: DaikinConfigEntry, add: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    thing = entry.data[CONF_THING_NAME]
    add(DaikinLocalSensor(coordinator, d, thing, entry.title) for d in SENSORS)


class DaikinLocalSensor(CoordinatorEntity[DaikinLocalCoordinator], SensorEntity):
    _attr_has_entity_name = True
    entity_description: DaikinSensorDescription

    def __init__(
        self, coordinator: DaikinLocalCoordinator, description: DaikinSensorDescription, thing: str, title: str
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{thing}_{description.key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, thing)}, name=title, manufacturer="Daikin")

    @property
    def available(self) -> bool:
        return super().available and self.entity_description.field in (self.coordinator.data or {})

    @property
    def native_value(self):
        raw = (self.coordinator.data or {}).get(self.entity_description.field)
        if raw is None:
            return None
        return _to_float(raw) if self.entity_description.numeric else str(raw)
