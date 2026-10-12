"""Climate entity using Home Assistant's native mode, fan, swing and preset vocabulary."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    PRESET_BOOST,
    PRESET_NONE,
    PRESET_SLEEP,
    SWING_OFF,
    SWING_VERTICAL,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_WHOLE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DaikinConfigEntry
from .const import CONF_FIVE_SPEED, CONF_THING_NAME, MAX_TEMP, MIN_TEMP
from .coordinator import DaikinLocalCoordinator
from .entity import DaikinLocalEntity
from .protocol import (
    F_MODE,
    F_ONOFF,
    F_ROOM_TEMP,
    F_SILENT,
    F_SLEEP,
    F_SWING,
    F_TEMP,
    F_TURBO,
    F_UDLVR,
    MODE_TO_VALUE,
    SWING_FULL,
    VALUE_TO_MODE,
    fan_level_from_state,
    fan_payload,
    fan_table,
    to_float,
    to_int,
)

# Coordinator-based platform: entity updates are serialised by the coordinator.
PARALLEL_UPDATES = 0

HVAC_FROM_MODE: dict[str, HVACMode] = {
    "cool": HVACMode.COOL,
    "dry": HVACMode.DRY,
    "fan": HVACMode.FAN_ONLY,
    "heat": HVACMode.HEAT,
    "auto": HVACMode.AUTO,
}
MODE_FROM_HVAC = {v: k for k, v in HVAC_FROM_MODE.items()}

PRESET_SILENT = "silent"  # not in HA's canonical list; translated via strings.json
PRESET_FIELDS = {PRESET_BOOST: F_TURBO, PRESET_SILENT: F_SILENT, PRESET_SLEEP: F_SLEEP}


async def async_setup_entry(hass: HomeAssistant, entry: DaikinConfigEntry, add: AddEntitiesCallback) -> None:
    add([DaikinLocalClimate(entry.runtime_data, entry)])


class DaikinLocalClimate(DaikinLocalEntity, ClimateEntity):
    _attr_name = None
    _attr_translation_key = "aircon"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_WHOLE
    _attr_target_temperature_step = 1.0
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_hvac_modes = [HVACMode.OFF, *HVAC_FROM_MODE.values()]
    _attr_swing_modes = [SWING_OFF, SWING_VERTICAL]
    _attr_preset_modes = [PRESET_NONE, *PRESET_FIELDS]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.SWING_MODE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: DaikinLocalCoordinator, entry: DaikinConfigEntry) -> None:
        super().__init__(coordinator, entry.data[CONF_THING_NAME], "climate")
        self._force_five = bool(entry.data.get(CONF_FIVE_SPEED, False))

    @property
    def _fan_table(self) -> dict[str, tuple[int, int | None]]:
        return fan_table(self.state_doc, self._force_five)

    @property
    def fan_modes(self) -> list[str]:
        return list(self._fan_table)

    @property
    def hvac_mode(self) -> HVACMode | None:
        if to_int(self.state_doc.get(F_ONOFF)) == 0:
            return HVACMode.OFF
        mode = VALUE_TO_MODE.get(to_int(self.state_doc.get(F_MODE)) or -1)
        return HVAC_FROM_MODE.get(mode) if mode else None

    @property
    def current_temperature(self) -> float | None:
        return to_float(self.state_doc.get(F_ROOM_TEMP))  # absent on adapter firmware V1.2.3

    @property
    def target_temperature(self) -> float | None:
        return to_float(self.state_doc.get(F_TEMP))

    @property
    def fan_mode(self) -> str | None:
        return fan_level_from_state(self.state_doc, self._fan_table)

    @property
    def swing_mode(self) -> str | None:
        swing = to_int(self.state_doc.get(F_SWING))
        if swing is None:
            return None
        return SWING_VERTICAL if swing == 1 else SWING_OFF

    @property
    def preset_mode(self) -> str:
        for preset, field in PRESET_FIELDS.items():
            if to_int(self.state_doc.get(field)) == 1:
                return preset
        return PRESET_NONE

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_set({F_ONOFF: 0})
            return
        await self.coordinator.async_set({F_ONOFF: 1, F_MODE: MODE_TO_VALUE[MODE_FROM_HVAC[hvac_mode]]})

    async def async_turn_on(self) -> None:
        await self.coordinator.async_set({F_ONOFF: 1})

    async def async_turn_off(self) -> None:
        await self.coordinator.async_set({F_ONOFF: 0})

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is not None:
            await self.coordinator.async_set({F_TEMP: round(temp)})

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self.coordinator.async_set(fan_payload(fan_mode, self._fan_table))

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        if swing_mode == SWING_VERTICAL:
            swing, louver = SWING_FULL
        else:
            # Keep the current fixed louver position (select entity) when stopping the swing.
            current = to_int(self.state_doc.get(F_UDLVR)) or 0
            swing, louver = 0, current if current != 15 else 0
        await self.coordinator.async_set({F_SWING: swing, F_UDLVR: louver})

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        payload = dict.fromkeys(PRESET_FIELDS.values(), 0)
        if preset_mode in PRESET_FIELDS:
            payload[PRESET_FIELDS[preset_mode]] = 1
        await self.coordinator.async_set(payload)
