"""Shared base entity."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .cloud_coordinator import DaikinCloudCoordinator
from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import DaikinLocalCoordinator
from .protocol import F_VERSION


class DaikinLocalEntity(CoordinatorEntity[DaikinLocalCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: DaikinLocalCoordinator, thing_name: str, key: str) -> None:
        super().__init__(coordinator)
        self._thing_name = thing_name
        self._attr_unique_id = f"{thing_name}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, thing_name)},
            name=coordinator.config_entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
            sw_version=(coordinator.data or {}).get(F_VERSION),
        )

    @property
    def state_doc(self) -> dict[str, Any]:
        return self.coordinator.data or {}


class DaikinCloudEntity(CoordinatorEntity[DaikinCloudCoordinator]):
    """Entity fed by the optional cloud poll; attached to the same device as the local entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DaikinCloudCoordinator, thing_name: str, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{thing_name}_cloud_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, thing_name)},
            name=coordinator.config_entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    @property
    def shadow(self) -> dict[str, Any]:
        return self.coordinator.data or {}
