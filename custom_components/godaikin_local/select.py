"""Select entity for the fixed vertical louver position (positions 1-5, or off)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DaikinConfigEntry
from .const import CONF_THING_NAME
from .coordinator import DaikinLocalCoordinator
from .entity import DaikinLocalEntity
from .protocol import F_SWING, F_UDLVR, LOUVER_POSITIONS, to_int

# Coordinator-based platform: entity updates are serialised by the coordinator.
PARALLEL_UPDATES = 0

OPTION_OFF = "off"
OPTION_SWING = "swing"
OPTIONS = [OPTION_OFF, *(f"position_{n}" for n in LOUVER_POSITIONS), OPTION_SWING]


async def async_setup_entry(hass: HomeAssistant, entry: DaikinConfigEntry, add: AddEntitiesCallback) -> None:
    add([DaikinLouverSelect(entry.runtime_data.local, entry.data[CONF_THING_NAME])])


class DaikinLouverSelect(DaikinLocalEntity, SelectEntity):
    _attr_translation_key = "louver"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = OPTIONS

    def __init__(self, coordinator: DaikinLocalCoordinator, thing_name: str) -> None:
        super().__init__(coordinator, thing_name, "louver")

    @property
    def current_option(self) -> str | None:
        swing = to_int(self.state_doc.get(F_SWING))
        louver = to_int(self.state_doc.get(F_UDLVR))
        if swing is None:
            return None
        if swing == 1:
            return OPTION_SWING
        if louver in LOUVER_POSITIONS:
            return f"position_{louver}"
        return OPTION_OFF

    async def async_select_option(self, option: str) -> None:
        if option == OPTION_SWING:
            payload = {F_SWING: 1, F_UDLVR: 15}
        elif option == OPTION_OFF:
            payload = {F_SWING: 0, F_UDLVR: 0}
        else:
            payload = {F_SWING: 0, F_UDLVR: int(option.rsplit("_", 1)[1])}
        await self.coordinator.async_set(payload)
