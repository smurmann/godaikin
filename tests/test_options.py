"""Capabilities: detection from the cloud shadow, option overrides, and their effect on the climate entity."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.godaikin.capabilities import resolve
from custom_components.godaikin.cloud import CloudUnit, capabilities_from_shadow
from custom_components.godaikin.const import (
    CONF_CAPABILITIES,
    CONF_FIVE_SPEED,
    DOMAIN,
    OPT_FAN_SPEEDS,
    OPT_HVAC_MODES,
    OPT_PRESETS,
    OPT_SWING,
)

from .conftest import ENTRY_DATA, HOST, KEY, THING

# Flags exactly as two live cooling-only units reported them (2026-10-13).
COOL_ONLY_SHADOW = {
    "Bar_DryM": 0, "Bar_CoolM": 0, "Bar_HeatM": 1, "Bar_AutoM": 1, "Bar_FanM": 0,
    "Bar_LowF": 0, "Bar_AutoF": 0, "Bar_Sleep": 0, "Bar_Swing": 0,
    "Ena_Silent": 1, "Ena_Turbo": 1, "Inf_IDAlgo": 0,
}  # fmt: skip


def test_capabilities_from_shadow() -> None:
    caps = capabilities_from_shadow(COOL_ONLY_SHADOW)
    assert caps == {
        "hvac_modes": ["cool", "dry", "fan"],
        "presets": ["boost", "silent", "sleep"],
        "fan_speeds": 5,
        "swing": True,
        "fan_auto": True,
        "fan_low": True,
    }
    heat_pump = {
        **COOL_ONLY_SHADOW,
        "Bar_HeatM": 0,
        "Bar_AutoM": 0,
        "Inf_IDAlgo": 1,
        "Ena_Turbo": 0,
        "Bar_Swing": 1,
    }
    caps = capabilities_from_shadow(heat_pump)
    assert caps["hvac_modes"] == ["cool", "dry", "fan", "heat", "auto"]
    assert caps["presets"] == ["silent", "sleep"]
    assert caps["fan_speeds"] == 3 and caps["swing"] is False
    # garbage / missing flags fall back safely
    assert capabilities_from_shadow({"Bar_CoolM": "x"})["hvac_modes"] == [
        "cool",
        "dry",
        "fan",
        "heat",
        "auto",
    ]
    assert capabilities_from_shadow(
        {"Bar_CoolM": 1, "Bar_DryM": 1, "Bar_FanM": 1, "Bar_HeatM": 1, "Bar_AutoM": 1}
    )["hvac_modes"] == ["cool"]
    assert CloudUnit(THING, "k", "n", COOL_ONLY_SHADOW).capabilities["fan_speeds"] == 5


def test_resolve_precedence(hass: HomeAssistant) -> None:
    # legacy entry without capabilities: conservative defaults
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA)
    caps = resolve(entry)
    assert caps.hvac_modes == ("cool", "dry", "fan") and caps.fan_speeds == 3 and caps.swing

    # legacy five-speed flag still honoured
    assert resolve(MockConfigEntry(domain=DOMAIN, data={**ENTRY_DATA, CONF_FIVE_SPEED: True})).fan_speeds == 5

    # detected capabilities
    detected = capabilities_from_shadow(COOL_ONLY_SHADOW)
    entry = MockConfigEntry(domain=DOMAIN, data={**ENTRY_DATA, CONF_CAPABILITIES: detected})
    assert resolve(entry).fan_speeds == 5
    assert resolve(entry).presets == ("boost", "silent", "sleep")

    # options override everything, and ordering follows ALL_MODES
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**ENTRY_DATA, CONF_CAPABILITIES: detected},
        options={OPT_HVAC_MODES: ["auto", "cool"], OPT_PRESETS: [], OPT_FAN_SPEEDS: "3", OPT_SWING: False},
    )
    caps = resolve(entry)
    assert (
        caps.hvac_modes == ("cool", "auto") and caps.presets == () and caps.fan_speeds == 3 and not caps.swing
    )


async def test_climate_sized_by_capabilities(
    hass: HomeAssistant, mock_unit: MagicMock, status: dict[str, Any]
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Office AC",
        data={**ENTRY_DATA, CONF_CAPABILITIES: capabilities_from_shadow(COOL_ONLY_SHADOW)},
        unique_id=THING,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get("climate.office_ac")
    assert state.attributes["hvac_modes"] == ["off", "cool", "dry", "fan_only"]
    assert state.attributes["preset_modes"] == ["none", "boost", "silent", "sleep"]
    assert len(state.attributes["fan_modes"]) == 6  # 5-speed handset per Inf_IDAlgo


async def test_options_flow_overrides(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {OPT_HVAC_MODES: ["cool", "heat"], OPT_PRESETS: ["silent"], OPT_FAN_SPEEDS: "5", OPT_SWING: False},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    state = hass.states.get("climate.office_ac")
    assert state.attributes["hvac_modes"] == ["off", "cool", "heat"]
    assert state.attributes["preset_modes"] == ["none", "silent"]
    assert "swing_modes" not in state.attributes
    assert len(state.attributes["fan_modes"]) == 6


async def test_options_no_presets_hides_feature(hass: HomeAssistant, mock_unit: MagicMock) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Office AC", data=ENTRY_DATA, unique_id=THING, options={OPT_PRESETS: []}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert "preset_modes" not in hass.states.get("climate.office_ac").attributes


@pytest.mark.usefixtures("mock_unit", "mock_discovery")
async def test_cloud_setup_stores_capabilities(hass: HomeAssistant, mock_cloud: MagicMock) -> None:
    mock_cloud.async_get_units.return_value = [CloudUnit(THING, KEY, "Office AC", COOL_ONLY_SHADOW)]
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "cloud"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "me@example.com", CONF_PASSWORD: "pw"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"thing_name": THING})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST, CONF_NAME: "Office AC", CONF_PORT: 7002, CONF_FIVE_SPEED: False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CAPABILITIES]["hvac_modes"] == ["cool", "dry", "fan"]
