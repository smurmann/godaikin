"""Diagnostic sensors.  Only created for fields the unit actually reports (firmware V1.2.3 reports none)."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DaikinConfigEntry
from .cloud_coordinator import DaikinCloudCoordinator
from .const import CONF_THING_NAME
from .coordinator import DaikinLocalCoordinator
from .entity import DaikinCloudEntity, DaikinLocalEntity
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


def _temp(key: str, field: str, category: EntityCategory | None = None) -> DaikinSensorDescription:
    return DaikinSensorDescription(
        key=key,
        field=field,
        translation_key=key,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=category,
    )


# Values the unit reports only to the cloud shadow (verified live on adapter firmware V1.2.3).
CLOUD_SENSORS: tuple[DaikinSensorDescription, ...] = (
    _temp("cloud_room_temperature", "Sta_IDRoomTemp"),
    _temp("cloud_indoor_coil_temperature", "Sta_IDCoilTemp", EntityCategory.DIAGNOSTIC),
    _temp("cloud_outdoor_temperature", "Sta_ODAirTemp"),
    _temp("cloud_outdoor_coil_temperature", "Sta_ODCoilTemp", EntityCategory.DIAGNOSTIC),
    _temp("cloud_discharge_temperature", "Sta_ODDiscTemp", EntityCategory.DIAGNOSTIC),
    DaikinSensorDescription(
        key="cloud_humidity",
        field="Sta_IDRh",
        translation_key="cloud_humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    ),
    DaikinSensorDescription(
        key="cloud_power",
        field="Sta_ODPwrCon",
        translation_key="cloud_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    DaikinSensorDescription(
        key="cloud_compressor_frequency",
        field="Sta_ODCpFreq",
        translation_key="cloud_compressor_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DaikinSensorDescription(
        key="cloud_compressor_runtime",
        field="Sta_CpRT",
        translation_key="cloud_compressor_runtime",
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DaikinSensorDescription(
        key="cloud_dc_bus_voltage",
        field="Sta_DCBus",
        translation_key="cloud_dc_bus_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DaikinSensorDescription(
        key="cloud_error_code",
        field="Sta_ErrCode",
        translation_key="cloud_error_code",
        numeric=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: DaikinConfigEntry, add: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data.local
    present = coordinator.data or {}
    thing = entry.data[CONF_THING_NAME]
    entities: list[SensorEntity] = [
        DaikinLocalSensor(coordinator, description, thing)
        for description in SENSORS
        if description.field in present
    ]
    if (cloud := entry.runtime_data.cloud) is not None:
        shadow = cloud.data or {}
        entities.extend(
            DaikinCloudSensor(cloud, description, thing)
            for description in CLOUD_SENSORS
            if description.field in shadow
        )
    add(entities)


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


class DaikinCloudSensor(DaikinCloudEntity, SensorEntity):
    entity_description: DaikinSensorDescription

    def __init__(
        self, coordinator: DaikinCloudCoordinator, description: DaikinSensorDescription, thing_name: str
    ) -> None:
        super().__init__(coordinator, thing_name, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | None:
        raw = self.shadow.get(self.entity_description.field)
        if raw is None:
            return None
        return to_float(raw) if self.entity_description.numeric else str(raw)
