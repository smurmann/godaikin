"""Polling coordinator for one GO DAIKIN unit."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, KEY_FAILURES_BEFORE_REAUTH, SCAN_INTERVAL
from .protocol import DaikinKeyError, DaikinLocalError, DaikinLocalUnit

_LOGGER = logging.getLogger(__name__)


class DaikinLocalCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetches the unit's status document and applies state changes."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, unit: DaikinLocalUnit) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=SCAN_INTERVAL,
        )
        self.unit = unit
        self._key_failures = 0

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await self.unit.async_get_status()
        except DaikinKeyError as err:
            self._key_failures += 1
            if self._key_failures >= KEY_FAILURES_BEFORE_REAUTH:
                raise ConfigEntryAuthFailed(f"Unit key rejected by {self.unit.host}: {err}") from err
            raise UpdateFailed(f"Could not decrypt reply from {self.unit.host}: {err}") from err
        except (DaikinLocalError, OSError, TimeoutError) as err:
            raise UpdateFailed(f"Cannot reach {self.unit.host}: {err}") from err
        except ValueError as err:  # malformed JSON frame
            raise UpdateFailed(f"Malformed reply from {self.unit.host}: {err}") from err
        self._key_failures = 0
        return data

    async def async_set(self, state: dict[str, Any]) -> None:
        """Send a partial state update and refresh."""
        try:
            await self.unit.async_set_status(state)
        except DaikinKeyError as err:
            raise ConfigEntryAuthFailed(f"Unit key rejected by {self.unit.host}: {err}") from err
        except (DaikinLocalError, OSError, TimeoutError, ValueError) as err:
            raise UpdateFailed(f"Command to {self.unit.host} failed: {err}") from err
        if self.data:
            self.async_set_updated_data({**self.data, **state})
        await self.async_request_refresh()
