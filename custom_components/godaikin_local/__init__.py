"""GO DAIKIN Local: LAN control of Daikin Malaysia/Singapore/Indonesia Wi-Fi air conditioners.

Control is LAN-only.  The cloud is contacted during setup to obtain each unit's local key
and, only if the user opts in, polled for sensor values the unit does not expose locally.
See docs/PROTOCOL.md for how the protocol was recovered.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant

from .cloud_coordinator import DaikinCloudCoordinator
from .const import (
    CONF_CLOUD_REFRESH_TOKEN,
    CONF_CLOUD_SUBJECT,
    CONF_THING_NAME,
    CONF_UNIT_KEY,
    DEFAULT_CLOUD_INTERVAL,
    DEFAULT_PORT,
    OPT_CLOUD,
    OPT_CLOUD_INTERVAL,
)
from .coordinator import DaikinLocalCoordinator
from .protocol import DaikinLocalUnit

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


@dataclass(slots=True)
class RuntimeData:
    local: DaikinLocalCoordinator
    cloud: DaikinCloudCoordinator | None = None


type DaikinConfigEntry = ConfigEntry[RuntimeData]


def cloud_enabled(entry: ConfigEntry) -> bool:
    return bool(entry.options.get(OPT_CLOUD)) and bool(
        entry.data.get(CONF_CLOUD_REFRESH_TOKEN) and entry.data.get(CONF_CLOUD_SUBJECT)
    )


async def async_setup_entry(hass: HomeAssistant, entry: DaikinConfigEntry) -> bool:
    unit = DaikinLocalUnit(
        host=entry.data[CONF_HOST],
        thing_name=entry.data[CONF_THING_NAME],
        unit_key=entry.data[CONF_UNIT_KEY],
        port=entry.data.get(CONF_PORT, DEFAULT_PORT),
    )
    local = DaikinLocalCoordinator(hass, entry, unit)
    await local.async_config_entry_first_refresh()

    cloud: DaikinCloudCoordinator | None = None
    if cloud_enabled(entry):
        cloud = DaikinCloudCoordinator(
            hass, entry, int(entry.options.get(OPT_CLOUD_INTERVAL, DEFAULT_CLOUD_INTERVAL))
        )
        # Cloud is optional: a failure here must not take the local control down.
        await cloud.async_refresh()

    entry.runtime_data = RuntimeData(local=local, cloud=cloud)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Options (features, cloud sensors) change entity shape: reload on change.
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: DaikinConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: DaikinConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
