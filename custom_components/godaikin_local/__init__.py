"""GO DAIKIN Local: LAN control of Daikin Malaysia/Singapore Wi-Fi air conditioners.

No cloud involved. See docs/PROTOCOL.md for how the protocol was recovered.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant

from .const import CONF_THING_NAME, CONF_UNIT_KEY, DEFAULT_PORT, DOMAIN
from .coordinator import DaikinLocalCoordinator
from .protocol import DaikinLocalUnit

PLATFORMS = [Platform.CLIMATE, Platform.SENSOR]

type DaikinConfigEntry = ConfigEntry[DaikinLocalCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: DaikinConfigEntry) -> bool:
    unit = DaikinLocalUnit(
        host=entry.data[CONF_HOST],
        thing_name=entry.data[CONF_THING_NAME],
        unit_key=entry.data[CONF_UNIT_KEY],
        port=entry.data.get(CONF_PORT, DEFAULT_PORT),
    )
    coordinator = DaikinLocalCoordinator(hass, unit, entry.title)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DaikinConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
