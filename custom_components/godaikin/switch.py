"""Switch entity for the ioniser (Set_Ion)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DaikinConfigEntry
from .const import CONF_THING_NAME
from .coordinator import DaikinLocalCoordinator
from .entity import DaikinLocalEntity
from .protocol import F_ION, to_int

# Coordinator-based platform: entity updates are serialised by the coordinator.
PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: DaikinConfigEntry, add: AddEntitiesCallback) -> None:
    add([DaikinIonSwitch(entry.runtime_data.local, entry.data[CONF_THING_NAME])])


class DaikinIonSwitch(DaikinLocalEntity, SwitchEntity):
    _attr_translation_key = "ioniser"
    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DaikinLocalCoordinator, thing_name: str) -> None:
        super().__init__(coordinator, thing_name, "ion")

    @property
    def available(self) -> bool:
        return super().available and F_ION in self.state_doc

    @property
    def is_on(self) -> bool | None:
        value = to_int(self.state_doc.get(F_ION))
        return None if value is None else value == 1

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set({F_ION: 1})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set({F_ION: 0})
