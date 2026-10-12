"""Resolve what a unit supports: detected cloud capabilities, overridden by user options."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import (
    ALL_MODES,
    ALL_PRESETS,
    CONF_CAPABILITIES,
    CONF_FIVE_SPEED,
    DEFAULT_MODES,
    OPT_FAN_SPEEDS,
    OPT_HVAC_MODES,
    OPT_PRESETS,
    OPT_SWING,
)


@dataclass(frozen=True, slots=True)
class UnitCapabilities:
    hvac_modes: tuple[str, ...]  # subset of ALL_MODES, protocol mode names
    presets: tuple[str, ...]  # subset of ALL_PRESETS
    fan_speeds: int  # 3 or 5
    swing: bool
    fan_auto: bool = True
    fan_low: bool = True

    @property
    def five_speed(self) -> bool:
        return self.fan_speeds == 5


def resolve(entry: ConfigEntry) -> UnitCapabilities:
    """Options win over detected capabilities, which win over conservative defaults."""
    detected: dict[str, Any] = entry.data.get(CONF_CAPABILITIES) or {}
    options = entry.options

    modes = options.get(OPT_HVAC_MODES) or detected.get("hvac_modes") or DEFAULT_MODES
    presets = options.get(OPT_PRESETS)
    if presets is None:
        presets = detected.get("presets", ALL_PRESETS)

    if OPT_FAN_SPEEDS in options:
        fan_speeds = int(options[OPT_FAN_SPEEDS])
    elif "fan_speeds" in detected:
        fan_speeds = int(detected["fan_speeds"])
    else:
        fan_speeds = 5 if entry.data.get(CONF_FIVE_SPEED) else 3

    swing = bool(options.get(OPT_SWING, detected.get("swing", True)))
    return UnitCapabilities(
        hvac_modes=tuple(m for m in ALL_MODES if m in modes),
        presets=tuple(p for p in ALL_PRESETS if p in presets),
        fan_speeds=fan_speeds,
        swing=swing,
        fan_auto=bool(detected.get("fan_auto", True)),
        fan_low=bool(detected.get("fan_low", True)),
    )
