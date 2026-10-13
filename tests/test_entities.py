"""Climate, select, switch and sensor behaviour against the fake unit."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    ATTR_SWING_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_SWING_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.godaikin.const import CONF_FIVE_SPEED, DOMAIN

from .conftest import ENTRY_DATA, THING

CLIMATE = "climate.office_ac"
SELECT = "select.office_ac_louver_position"
SWITCH = "switch.office_ac_ioniser"


async def _call(hass: HomeAssistant, domain: str, service: str, **data: Any) -> None:
    await hass.services.async_call(domain, service, data, blocking=True)
    await hass.async_block_till_done()


async def test_climate_state_mapping(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    state = hass.states.get(CLIMATE)
    assert state.state == HVACMode.COOL
    assert state.attributes["temperature"] == 21
    assert state.attributes["fan_mode"] == "medium"
    assert state.attributes["fan_modes"] == ["auto", "low", "medium", "high"]
    assert state.attributes["swing_mode"] == "off"
    assert state.attributes["swing_modes"] == ["off", "vertical"]
    assert state.attributes["preset_mode"] == "none"
    assert state.attributes["preset_modes"] == ["none", "boost", "silent", "sleep"]
    assert state.attributes["hvac_modes"] == ["off", "cool", "dry", "fan_only"]  # conservative default
    assert state.attributes["min_temp"] == 16
    assert state.attributes["max_temp"] == 30
    assert "current_temperature" in state.attributes


@pytest.mark.parametrize(
    ("service", "data", "expected"),
    [
        (SERVICE_SET_HVAC_MODE, {ATTR_HVAC_MODE: HVACMode.OFF}, {"Set_OnOff": 0}),
        (SERVICE_SET_HVAC_MODE, {ATTR_HVAC_MODE: HVACMode.DRY}, {"Set_OnOff": 1, "Set_Mode": 4}),
        (SERVICE_SET_HVAC_MODE, {ATTR_HVAC_MODE: HVACMode.FAN_ONLY}, {"Set_OnOff": 1, "Set_Mode": 2}),
        (SERVICE_TURN_ON, {}, {"Set_OnOff": 1}),
        (SERVICE_TURN_OFF, {}, {"Set_OnOff": 0}),
        (SERVICE_SET_TEMPERATURE, {ATTR_TEMPERATURE: 23.6}, {"Set_Temp": 24}),
        (SERVICE_SET_FAN_MODE, {ATTR_FAN_MODE: "high"}, {"Set_Fan": 8}),
        (SERVICE_SET_FAN_MODE, {ATTR_FAN_MODE: "auto"}, {"Set_Fan": 128}),
        (SERVICE_SET_SWING_MODE, {ATTR_SWING_MODE: "vertical"}, {"Set_Swing": 1, "Set_UDLvr": 15}),
        (SERVICE_SET_SWING_MODE, {ATTR_SWING_MODE: "off"}, {"Set_Swing": 0, "Set_UDLvr": 3}),
        (
            SERVICE_SET_PRESET_MODE,
            {ATTR_PRESET_MODE: "boost"},
            {"Set_Turbo": 1, "Set_Silent": 0, "Set_Sleep": 0},
        ),
        (
            SERVICE_SET_PRESET_MODE,
            {ATTR_PRESET_MODE: "none"},
            {"Set_Turbo": 0, "Set_Silent": 0, "Set_Sleep": 0},
        ),
    ],
)
async def test_climate_commands(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    mock_unit: MagicMock,
    service: str,
    data: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    await _call(hass, CLIMATE_DOMAIN, service, **{ATTR_ENTITY_ID: CLIMATE, **data})
    mock_unit.async_set_status.assert_awaited_once_with(expected)


async def test_climate_optimistic_update_and_presets(
    hass: HomeAssistant, setup_entry: MockConfigEntry, status: dict[str, Any]
) -> None:
    await _call(
        hass, CLIMATE_DOMAIN, SERVICE_SET_PRESET_MODE, **{ATTR_ENTITY_ID: CLIMATE, ATTR_PRESET_MODE: "sleep"}
    )
    assert hass.states.get(CLIMATE).attributes["preset_mode"] == "sleep"
    await _call(
        hass, CLIMATE_DOMAIN, SERVICE_SET_SWING_MODE, **{ATTR_ENTITY_ID: CLIMATE, ATTR_SWING_MODE: "vertical"}
    )
    assert hass.states.get(CLIMATE).attributes["swing_mode"] == "vertical"
    assert hass.states.get(SELECT).state == "swing"
    await _call(
        hass, CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, **{ATTR_ENTITY_ID: CLIMATE, ATTR_TEMPERATURE: 26}
    )
    assert hass.states.get(CLIMATE).attributes["temperature"] == 26


async def test_climate_off_and_unknown_mode(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_unit: MagicMock, status: dict[str, Any]
) -> None:
    status.update({"Set_OnOff": 0, "Set_Mode": 99, "Set_Swing": None})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(CLIMATE)
    assert state.state == HVACMode.OFF
    assert state.attributes["swing_mode"] is None

    status.update({"Set_OnOff": 1})
    await _call(hass, CLIMATE_DOMAIN, SERVICE_TURN_ON, **{ATTR_ENTITY_ID: CLIMATE})
    assert hass.states.get(CLIMATE).state == "unknown"  # mode 99 is not in the table


async def test_five_speed_detection_and_forcing(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_unit: MagicMock, status: dict[str, Any]
) -> None:
    status.update({"Set_Fan": 8, "Set_FanExtend": 1})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(CLIMATE)
    assert state.attributes["fan_modes"] == ["auto", "low", "medium_low", "medium", "medium_high", "high"]
    assert state.attributes["fan_mode"] == "medium_high"
    await _call(
        hass, CLIMATE_DOMAIN, SERVICE_SET_FAN_MODE, **{ATTR_ENTITY_ID: CLIMATE, ATTR_FAN_MODE: "medium_low"}
    )
    mock_unit.async_set_status.assert_awaited_with({"Set_Fan": 2, "Set_FanExtend": 1})


async def test_forced_five_speed_on_three_speed_unit(
    hass: HomeAssistant, mock_unit: MagicMock, status: dict[str, Any]
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Office AC", data={**ENTRY_DATA, CONF_FIVE_SPEED: True}, unique_id=THING
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.get(CLIMATE).attributes["fan_modes"]) == 6


async def test_louver_select(hass: HomeAssistant, setup_entry: MockConfigEntry, mock_unit: MagicMock) -> None:
    state = hass.states.get(SELECT)
    assert state.state == "position_3"
    assert state.attributes["options"] == [
        "off",
        "position_1",
        "position_2",
        "position_3",
        "position_4",
        "position_5",
        "swing",
    ]
    await _call(hass, "select", "select_option", **{ATTR_ENTITY_ID: SELECT, "option": "position_5"})
    mock_unit.async_set_status.assert_awaited_with({"Set_Swing": 0, "Set_UDLvr": 5})
    await _call(hass, "select", "select_option", **{ATTR_ENTITY_ID: SELECT, "option": "swing"})
    mock_unit.async_set_status.assert_awaited_with({"Set_Swing": 1, "Set_UDLvr": 15})
    await _call(hass, "select", "select_option", **{ATTR_ENTITY_ID: SELECT, "option": "off"})
    mock_unit.async_set_status.assert_awaited_with({"Set_Swing": 0, "Set_UDLvr": 0})
    assert hass.states.get(SELECT).state == "off"


async def test_ioniser_switch(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_unit: MagicMock
) -> None:
    assert hass.states.get(SWITCH).state == STATE_ON
    await _call(hass, "switch", SERVICE_TURN_OFF, **{ATTR_ENTITY_ID: SWITCH})
    mock_unit.async_set_status.assert_awaited_with({"Set_Ion": 0})
    assert hass.states.get(SWITCH).state == STATE_OFF
    await _call(hass, "switch", SERVICE_TURN_ON, **{ATTR_ENTITY_ID: SWITCH})
    mock_unit.async_set_status.assert_awaited_with({"Set_Ion": 1})


async def test_switch_unavailable_without_field(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_unit: MagicMock, status: dict[str, Any]
) -> None:
    del status["Set_Ion"]
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(SWITCH).state == "unavailable"


async def test_sensors_only_for_reported_fields(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_unit: MagicMock, status: dict[str, Any]
) -> None:
    status.update({"Stat_IDRoomTemp": "27", "Stat_ErrorCode": "E0", "Sta_IDRh": None})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.office_ac_firmware_version").state == "V1.2.3"
    assert hass.states.get("sensor.office_ac_room_temperature").state == "27.0"
    assert hass.states.get("sensor.office_ac_error_code").state == "E0"
    assert hass.states.get("sensor.office_ac_humidity").state == "unknown"
    assert hass.states.get("sensor.office_ac_power") is None
