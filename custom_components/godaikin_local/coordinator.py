"""Polling coordinator for one GO DAIKIN unit."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .protocol import DaikinKeyError, DaikinLocalError, DaikinLocalUnit

_LOGGER = logging.getLogger(__name__)


class DaikinLocalCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, unit: DaikinLocalUnit, name: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {name}",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.unit = unit

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.unit.async_get_status()
        except DaikinKeyError as err:
            raise UpdateFailed(f"Key rejected by {self.unit.host}: {err}") from err
        except (DaikinLocalError, OSError, TimeoutError) as err:
            raise UpdateFailed(f"Cannot reach {self.unit.host}: {err}") from err
        except ValueError as err:  # includes json.JSONDecodeError on a malformed frame
            raise UpdateFailed(f"Malformed reply from {self.unit.host}: {err}") from err

    async def async_set(self, state: dict[str, Any]) -> None:
        await self.unit.async_set_status(state)
        # Optimistically merge so the UI updates before the next poll.
        if self.data:
            self.async_set_updated_data({**self.data, **state})
        await self.async_request_refresh()
