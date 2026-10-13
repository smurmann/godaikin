"""Power and energy estimation for units that do not meter themselves.

The outdoor units seen so far report neither power (``Sta_ODPwrCon``) nor compressor
frequency (``Sta_ODCpFreq``; ``Inf_ODPwrCon`` = 0 says the hardware cannot).  What they do
tell us is whether the unit is on, which mode and fan speed it is in and, through the cloud
shadow, whether the compressor is running.  From that and a rated input power we build a
coarse but useful estimate:

    off                                   -> 0 W
    on, compressor running                -> rated input power
    on, compressor idle / fan-only mode   -> indoor fan power (scaled by fan speed)

Energy is integrated from the estimated power between observations (left Riemann sum), which
is exactly how Home Assistant's own integration sensor would do it, but keeps the result
inside the integration so it restores across restarts and needs no YAML.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .protocol import F_FAN, F_MODE, F_ONOFF, VALUE_TO_MODE, to_int

# Rated cooling input (W) by Daikin capacity class (Inf_IDCap: 20 = 2.0 kW ... 71 = 7.1 kW).
# Typical nameplate values for R32 wall units; the user can override per unit.
RATED_INPUT_BY_CAPACITY: dict[int, int] = {20: 600, 25: 700, 35: 1100, 50: 1600, 60: 2000, 71: 2400}
DEFAULT_RATED_POWER_W = 1100
DEFAULT_FAN_POWER_W = 45  # indoor fan at medium speed

# Relative indoor-fan draw by Set_Fan value (auto ~ medium).
_FAN_SCALE: dict[int, float] = {128: 1.0, 2: 0.6, 4: 1.0, 8: 1.4}

# Cloud shadow fields
F_CP_ONOFF = "Sta_CpOnOff"
F_CP_RUNTIME = "Sta_CpRT"
F_CAPACITY = "Inf_IDCap"


def default_rated_power(capabilities: dict[str, Any] | None) -> int:
    """Rated input power from the capacity class recorded at cloud setup, else a 3.5 kW class."""
    cap = to_int((capabilities or {}).get("capacity"))
    return RATED_INPUT_BY_CAPACITY.get(cap or 0, DEFAULT_RATED_POWER_W)


def estimate_power_w(
    state: dict[str, Any],
    cloud: dict[str, Any] | None,
    rated_power_w: float,
    fan_power_w: float,
) -> float:
    """Instantaneous power estimate from the local status and, if present, the cloud shadow."""
    if to_int(state.get(F_ONOFF)) != 1:
        return 0.0
    fan = fan_power_w * _FAN_SCALE.get(to_int(state.get(F_FAN)) or 128, 1.0)
    mode = VALUE_TO_MODE.get(to_int(state.get(F_MODE)) or -1)
    if mode == "fan":
        return fan
    compressor = to_int((cloud or {}).get(F_CP_ONOFF))
    if compressor is None:
        # No cloud data: assume the compressor runs whenever a conditioning mode is selected.
        return rated_power_w
    return rated_power_w if compressor == 1 else fan


@dataclass
class EnergyAccumulator:
    """Integrates estimated power over wall-clock time into kWh."""

    total_kwh: float = 0.0
    last_power_w: float | None = None
    last_time: datetime | None = None

    def sample(self, power_w: float, now: datetime) -> float:
        """Record a new power reading; credit the previous reading for the elapsed interval."""
        if self.last_time is not None and self.last_power_w is not None:
            hours = (now - self.last_time).total_seconds() / 3600
            if hours > 0:
                self.total_kwh += self.last_power_w * hours / 1000
        self.last_power_w, self.last_time = power_w, now
        return self.total_kwh
