"""Diagnostics with the unit key redacted."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import DaikinConfigEntry
from .const import CONF_UNIT_KEY

TO_REDACT = {CONF_UNIT_KEY, "key", "cloud_refresh_token", "cloud_subject"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: DaikinConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data.local
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "status": async_redact_data(coordinator.data or {}, TO_REDACT),
        "last_update_success": coordinator.last_update_success,
        "cloud": async_redact_data(entry.runtime_data.cloud.data or {}, TO_REDACT)
        if entry.runtime_data.cloud
        else None,
    }
