"""Binary sensors from the optional cloud poll: compressor running, cloud link, presence."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DaikinConfigEntry
from .cloud_coordinator import DaikinCloudCoordinator
from .const import CONF_THING_NAME
from .entity import DaikinCloudEntity
from .protocol import to_int

# Coordinator-based platform: entity updates are serialised by the coordinator.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class DaikinBinaryDescription(BinarySensorEntityDescription):
    field: str
    on_value: object = 1


CLOUD_BINARY_SENSORS: tuple[DaikinBinaryDescription, ...] = (
    DaikinBinaryDescription(
        key="compressor",
        field="Sta_CpOnOff",
        translation_key="compressor",
        device_class=BinarySensorDeviceClass.RUNNING,
    ),
    DaikinBinaryDescription(
        key="cloud_connected",
        field="eventType",
        on_value="connected",
        translation_key="cloud_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DaikinBinaryDescription(
        key="presence",
        field="Sta_HumanDct",
        translation_key="presence",
        device_class=BinarySensorDeviceClass.OCCUPANCY,
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: DaikinConfigEntry, add: AddEntitiesCallback) -> None:
    cloud = entry.runtime_data.cloud
    if cloud is None:
        return
    shadow = cloud.data or {}
    add(
        DaikinCloudBinarySensor(cloud, description, entry.data[CONF_THING_NAME])
        for description in CLOUD_BINARY_SENSORS
        if description.field in shadow
    )


class DaikinCloudBinarySensor(DaikinCloudEntity, BinarySensorEntity):
    entity_description: DaikinBinaryDescription

    def __init__(
        self, coordinator: DaikinCloudCoordinator, description: DaikinBinaryDescription, thing_name: str
    ) -> None:
        super().__init__(coordinator, thing_name, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        raw = self.shadow.get(self.entity_description.field)
        if raw is None:
            return None
        expected = self.entity_description.on_value
        return raw == expected if isinstance(expected, str) else to_int(raw) == expected
