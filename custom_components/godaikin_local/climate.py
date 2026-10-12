"""Climate entity backed by the local TCP/AES protocol.

Verified live 2026-10-12: getStatus returns a flat document, e.g.
{"thingName":..,"version":"V1.2.3","eventType":"connected","Set_OnOff":0,"Set_Fan":4,
 "Set_Mode":1,"Set_Temp":21,"Set_Turbo":0,"Set_Silent":0,"Set_Sleep":0,"Set_Ion":1,
 "Set_Swing":0,"Set_UDLvr":0,...}.  No room temperature is exposed locally.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    PRESET_BOOST,
    PRESET_NONE,
    PRESET_SLEEP,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DaikinConfigEntry
from .const import CONF_FIVE_SPEED, CONF_THING_NAME, DOMAIN, MAX_TEMP, MIN_TEMP
from .coordinator import DaikinLocalCoordinator
from .protocol import (
    F_ION,
    F_MODE,
    F_ONOFF,
    F_ROOM_TEMP,
    F_SILENT,
    F_SLEEP,
    F_SWING,
    F_TEMP,
    F_TURBO,
    F_UDLVR,
    F_UPDATED,
    F_VERSION,
    FAN_TO_VALUE_3,
    FAN_TO_VALUE_5,
    MODE_TO_VALUE,
    SWING_FULL,
    SWING_OFF,
    VALUE_TO_MODE,
    _to_float,
    _to_int,
    fan_level_from_state,
    fan_payload,
    is_five_speed,
)

HVAC_FROM_MODE = {
    "cool": HVACMode.COOL,
    "dry": HVACMode.DRY,
    "fan": HVACMode.FAN_ONLY,
    "heat": HVACMode.HEAT,
    "auto": HVACMode.AUTO,
}
MODE_FROM_HVAC = {v: k for k, v in HVAC_FROM_MODE.items()}

SWING_MODES = ["off", "swing", "position1", "position2", "position3", "position4", "position5"]
PRESET_SILENT = "silent"
PRESETS = [PRESET_NONE, PRESET_BOOST, PRESET_SILENT, PRESET_SLEEP]
PRESET_FIELDS = {PRESET_BOOST: F_TURBO, PRESET_SILENT: F_SILENT, PRESET_SLEEP: F_SLEEP}


async def async_setup_entry(hass: HomeAssistant, entry: DaikinConfigEntry, add: AddEntitiesCallback) -> None:
    add([DaikinLocalClimate(entry.runtime_data, entry)])


class DaikinLocalClimate(CoordinatorEntity[DaikinLocalCoordinator], ClimateEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1.0
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_hvac_modes = [HVACMode.OFF, *HVAC_FROM_MODE.values()]
    _attr_swing_modes = SWING_MODES
    _attr_preset_modes = PRESETS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.SWING_MODE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: DaikinLocalCoordinator, entry: DaikinConfigEntry) -> None:
        super().__init__(coordinator)
        self._force_five = bool(entry.data.get(CONF_FIVE_SPEED, False))
        thing = entry.data[CONF_THING_NAME]
        self._attr_unique_id = f"{thing}_climate"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, thing)},
            name=entry.title,
            manufacturer="Daikin",
            model="GO DAIKIN Wi-Fi unit",
            sw_version=(coordinator.data or {}).get(F_VERSION),
        )

    @property
    def _state(self) -> dict[str, Any]:
        return self.coordinator.data or {}

    @property
    def _five_speed(self) -> bool:
        return self._force_five or is_five_speed(self._state)

    @property
    def fan_modes(self) -> list[str]:
        return list(FAN_TO_VALUE_5 if self._five_speed else FAN_TO_VALUE_3)

    @property
    def hvac_mode(self) -> HVACMode | None:
        if _to_int(self._state.get(F_ONOFF)) == 0:
            return HVACMode.OFF
        mode = VALUE_TO_MODE.get(_to_int(self._state.get(F_MODE)) or -1)
        return HVAC_FROM_MODE.get(mode) if mode else None

    @property
    def current_temperature(self) -> float | None:
        return _to_float(self._state.get(F_ROOM_TEMP))  # not reported locally on tested firmware V1.2.3

    @property
    def target_temperature(self) -> float | None:
        return _to_float(self._state.get(F_TEMP))

    @property
    def fan_mode(self) -> str | None:
        return fan_level_from_state(self._state, self._five_speed)

    @property
    def swing_mode(self) -> str | None:
        swing = _to_int(self._state.get(F_SWING))
        lvr = _to_int(self._state.get(F_UDLVR))
        if swing == 1:
            return "swing"
        if swing == 0 and lvr and 1 <= lvr <= 5:
            return f"position{lvr}"
        if swing == 0:
            return "off"
        return None

    @property
    def preset_mode(self) -> str | None:
        for preset, field in PRESET_FIELDS.items():
            if _to_int(self._state.get(field)) == 1:
                return preset
        return PRESET_NONE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self._state
        return {
            "ion": _to_int(s.get(F_ION)),
            "firmware_version": s.get(F_VERSION),
            "updated_on": s.get(F_UPDATED),
            "thing_name": s.get("thingName"),
            "event_type": s.get("eventType"),
        }

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
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        await self.coordinator.async_set({F_TEMP: int(round(temp))})

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self.coordinator.async_set(fan_payload(fan_mode, self._five_speed))

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        if swing_mode == "swing":
            swing, lvr = SWING_FULL
        elif swing_mode.startswith("position"):
            swing, lvr = 0, int(swing_mode[-1])
        else:
            swing, lvr = SWING_OFF
        await self.coordinator.async_set({F_SWING: swing, F_UDLVR: lvr})

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        payload = {field: 0 for field in PRESET_FIELDS.values()}
        if preset_mode in PRESET_FIELDS:
            payload[PRESET_FIELDS[preset_mode]] = 1
        await self.coordinator.async_set(payload)
