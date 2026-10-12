"""Setup, unload, coordinator error handling and diagnostics."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.godaikin_local.const import DOMAIN, KEY_FAILURES_BEFORE_REAUTH, SCAN_INTERVAL
from custom_components.godaikin_local.diagnostics import async_get_config_entry_diagnostics
from custom_components.godaikin_local.protocol import DaikinKeyError, DaikinLocalError

from .conftest import THING


async def test_setup_and_unload(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    assert setup_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("climate.office_ac") is not None
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, THING)})
    assert device is not None
    assert device.sw_version == "V1.2.3"

    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert setup_entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_retries_when_unreachable(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_unit: MagicMock
) -> None:
    mock_unit.async_get_status.side_effect = DaikinLocalError("no data")
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def _advance(hass: HomeAssistant) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + SCAN_INTERVAL + timedelta(seconds=1))
    await hass.async_block_till_done()


async def test_unavailable_then_recovers(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_unit: MagicMock
) -> None:
    mock_unit.async_get_status.side_effect = OSError("unreachable")
    await _advance(hass)
    assert hass.states.get("climate.office_ac").state == "unavailable"

    mock_unit.async_get_status.side_effect = ValueError("bad json")
    await _advance(hass)
    assert hass.states.get("climate.office_ac").state == "unavailable"

    mock_unit.async_get_status.side_effect = None
    await _advance(hass)
    assert hass.states.get("climate.office_ac").state == "cool"


async def test_repeated_key_failures_trigger_reauth(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_unit: MagicMock
) -> None:
    mock_unit.async_get_status.side_effect = DaikinKeyError("rejected")
    for _ in range(KEY_FAILURES_BEFORE_REAUTH):
        await _advance(hass)
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert flows and flows[0]["context"]["source"] == "reauth"


async def test_command_failure_is_translated_error(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_unit: MagicMock
) -> None:
    mock_unit.async_set_status.side_effect = DaikinLocalError("no data")
    with pytest.raises(HomeAssistantError) as excinfo:
        await hass.services.async_call(
            "climate", "set_temperature", {"entity_id": "climate.office_ac", "temperature": 24}, blocking=True
        )
    assert excinfo.value.translation_key == "command_failed"
    assert excinfo.value.translation_domain == DOMAIN


async def test_command_key_rejected_triggers_reauth(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_unit: MagicMock
) -> None:
    mock_unit.async_set_status.side_effect = DaikinKeyError("rejected")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "climate", "turn_off", {"entity_id": "climate.office_ac"}, blocking=True
        )
    await hass.async_block_till_done()
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_diagnostics_redact_key(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    diag: dict[str, Any] = await async_get_config_entry_diagnostics(hass, setup_entry)
    assert diag["entry"]["unit_key"] == "**REDACTED**"
    assert diag["status"]["Set_Temp"] == 21
    assert diag["last_update_success"] is True
