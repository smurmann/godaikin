"""Constants for the GO DAIKIN Local integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "godaikin_local"
MANUFACTURER = "Daikin"
MODEL = "GO DAIKIN Wi-Fi air conditioner"

CONF_THING_NAME = "thing_name"
CONF_UNIT_KEY = "unit_key"
CONF_FIVE_SPEED = "five_speed_fan"  # legacy flag kept for entries created before 0.4
CONF_CAPABILITIES = "capabilities"  # dict stored in entry.data at cloud setup

# Options (entry.options) override detected capabilities.
OPT_HVAC_MODES = "hvac_modes"
OPT_PRESETS = "presets"
OPT_FAN_SPEEDS = "fan_speeds"
OPT_SWING = "swing"

ALL_MODES = ["cool", "dry", "fan", "heat", "auto"]
DEFAULT_MODES = ["cool", "dry", "fan"]  # what every GO DAIKIN unit seen so far supports
ALL_PRESETS = ["boost", "silent", "sleep"]
FAN_SPEED_CHOICES = ["3", "5"]

DEFAULT_PORT = 7002
SCAN_INTERVAL = timedelta(seconds=30)

MIN_TEMP = 16.0
MAX_TEMP = 30.0

# Number of consecutive decrypt failures before the entry asks for re-authentication.
KEY_FAILURES_BEFORE_REAUTH = 3
