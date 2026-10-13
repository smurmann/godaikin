"""Optional cloud polling for sensor values the unit does not expose locally.

Opt-in per entry (Configure → "Cloud sensors").  Control stays on the LAN; this coordinator
only reads the unit's cloud shadow (room temperature, coil/outdoor temperatures, compressor
data, power, error code).  Sessions are renewed with a revocable refresh token stored on the
entry; the account password is never stored.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .cloud import CloudAuthError, CloudError, CloudSession, GoDaikinCloud
from .const import CONF_CLOUD_REFRESH_TOKEN, CONF_CLOUD_SUBJECT, CONF_THING_NAME, DOMAIN

_LOGGER = logging.getLogger(__name__)
_ACCOUNT_CACHE_KEY = f"{DOMAIN}_cloud_accounts"


@dataclass
class _AccountCache:
    """One cloud session and one shadow fetch per account, shared by every entry on it."""

    session: CloudSession | None = None
    shadows: dict[str, dict[str, Any]] = field(default_factory=dict)
    fetched_at: float = 0.0  # time.monotonic()
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _account_cache(hass: HomeAssistant, subject: str) -> _AccountCache:
    caches: dict[str, _AccountCache] = hass.data.setdefault(_ACCOUNT_CACHE_KEY, {})
    return caches.setdefault(subject, _AccountCache())


class DaikinCloudCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls gethomepageshadowstate and keeps this entry's unit document."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, interval_minutes: int) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} cloud {entry.title}",
            update_interval=timedelta(minutes=interval_minutes),
        )
        self._cloud = GoDaikinCloud(async_get_clientsession(hass))
        self._thing = entry.data[CONF_THING_NAME]
        self._account = _account_cache(hass, entry.data[CONF_CLOUD_SUBJECT])
        # Anything fetched for the account within this window is reused instead of re-polled,
        # so several entries on one account cost one call per interval.
        self._reuse_window = timedelta(minutes=interval_minutes).total_seconds() * 0.8

    async def _async_session(self) -> CloudSession:
        cache = self._account
        if cache.session is None or cache.session.expired():
            data = self.config_entry.data
            cache.session = await self._cloud.async_refresh(
                data[CONF_CLOUD_SUBJECT], data[CONF_CLOUD_REFRESH_TOKEN]
            )
        return cache.session

    async def _async_shadows(self) -> dict[str, dict[str, Any]]:
        cache = self._account
        async with cache.lock:
            if cache.shadows and time.monotonic() - cache.fetched_at < self._reuse_window:
                return cache.shadows
            try:
                shadows = await self._cloud.async_get_shadows(await self._async_session())
            except CloudAuthError:
                # Access token may have been revoked server-side: redeem the refresh token once more.
                cache.session = None
                shadows = await self._cloud.async_get_shadows(await self._async_session())
            cache.shadows, cache.fetched_at = shadows, time.monotonic()
            return shadows

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            shadows = await self._async_shadows()
        except CloudAuthError as err:
            raise ConfigEntryAuthFailed(f"GO DAIKIN cloud session expired: {err}") from err
        except CloudError as err:
            raise UpdateFailed(f"GO DAIKIN cloud unavailable: {err}") from err
        doc = shadows.get(self._thing)
        if doc is None:
            raise UpdateFailed(f"{self._thing} is not on this GO DAIKIN account")
        return doc
