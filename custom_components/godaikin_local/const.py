"""Constants for the GO DAIKIN Local integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "godaikin_local"
MANUFACTURER = "Daikin"
MODEL = "GO DAIKIN Wi-Fi air conditioner"

CONF_THING_NAME = "thing_name"
CONF_UNIT_KEY = "unit_key"
CONF_FIVE_SPEED = "five_speed_fan"

DEFAULT_PORT = 7002
SCAN_INTERVAL = timedelta(seconds=30)

MIN_TEMP = 16.0
MAX_TEMP = 30.0

# Number of consecutive decrypt failures before the entry asks for re-authentication.
KEY_FAILURES_BEFORE_REAUTH = 3
