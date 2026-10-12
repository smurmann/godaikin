"""GO DAIKIN Local: LAN control of Daikin Malaysia/Singapore/Indonesia Wi-Fi air conditioners.

The cloud is contacted once, during setup, to obtain each unit's local key.  After that all
traffic stays on the LAN.  See docs/PROTOCOL.md for how the protocol was recovered.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant

from .const import CONF_THING_NAME, CONF_UNIT_KEY, DEFAULT_PORT
from .coordinator import DaikinLocalCoordinator
from .protocol import DaikinLocalUnit

PLATFORMS: list[Platform] = [Platform.CLIMATE, Platform.SELECT, Platform.SENSOR, Platform.SWITCH]

type DaikinConfigEntry = ConfigEntry[DaikinLocalCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: DaikinConfigEntry) -> bool:
    unit = DaikinLocalUnit(
        host=entry.data[CONF_HOST],
        thing_name=entry.data[CONF_THING_NAME],
        unit_key=entry.data[CONF_UNIT_KEY],
        port=entry.data.get(CONF_PORT, DEFAULT_PORT),
    )
    coordinator = DaikinLocalCoordinator(hass, entry, unit)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DaikinConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
