"""Constants for the GO DAIKIN Local integration."""

DOMAIN = "godaikin_local"

CONF_THING_NAME = "thing_name"
CONF_UNIT_KEY = "unit_key"
CONF_FIVE_SPEED = "five_speed_fan"

DEFAULT_SCAN_INTERVAL = 30  # seconds; the app polls local units every 1 s, be gentler
DEFAULT_PORT = 7002

MIN_TEMP = 16.0
MAX_TEMP = 30.0
