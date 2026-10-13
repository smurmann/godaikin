"""Power/energy estimation: pure estimator, accumulator, sensors, options, hvac_mode-in-set_temperature."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.climate import ATTR_HVAC_MODE, HVACMode
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from custom_components.godaikin.const import CONF_CAPABILITIES, DOMAIN, OPT_FAN_POWER, OPT_RATED_POWER
from custom_components.godaikin.energy import (
    DEFAULT_RATED_POWER_W,
    EnergyAccumulator,
    default_rated_power,
    estimate_power_w,
)

from .conftest import ENTRY_DATA, THING
from .test_cloud import CLOUD_DATA, CLOUD_OPTIONS, SHADOW

ON_COOL = {"Set_OnOff": 1, "Set_Mode": 1, "Set_Fan": 4}


def test_estimate_power_w() -> None:
    assert estimate_power_w({"Set_OnOff": 0}, None, 1100, 45) == 0
    assert estimate_power_w(ON_COOL, None, 1100, 45) == 1100  # no cloud: assume compressor on
    assert estimate_power_w(ON_COOL, {"Sta_CpOnOff": 1}, 1100, 45) == 1100
    assert estimate_power_w(ON_COOL, {"Sta_CpOnOff": 0}, 1100, 45) == 45  # compressor paused -> fan only
    assert estimate_power_w({**ON_COOL, "Set_Mode": 2}, {"Sta_CpOnOff": 1}, 1100, 45) == 45  # fan mode
    assert estimate_power_w({**ON_COOL, "Set_Mode": 2, "Set_Fan": 8}, None, 1100, 50) == 70  # high fan
    assert estimate_power_w({**ON_COOL, "Set_Mode": 2, "Set_Fan": 2}, None, 1100, 50) == 30  # low fan
    assert estimate_power_w({**ON_COOL, "Set_Mode": 2, "Set_Fan": 128}, None, 1100, 50) == 50  # auto ~ medium
    assert estimate_power_w({}, None, 1100, 45) == 0


def test_default_rated_power() -> None:
    assert default_rated_power({"capacity": 35}) == 1100
    assert default_rated_power({"capacity": 50}) == 1600
    assert default_rated_power({"capacity": 99}) == DEFAULT_RATED_POWER_W
    assert default_rated_power(None) == DEFAULT_RATED_POWER_W


def test_accumulator() -> None:
    acc = EnergyAccumulator()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    assert acc.sample(1000, t0) == 0.0  # first sample: nothing elapsed
    assert acc.sample(500, t0 + timedelta(hours=1)) == pytest.approx(1.0)  # 1000 W for 1 h
    assert acc.sample(0, t0 + timedelta(hours=1, minutes=30)) == pytest.approx(1.25)  # 500 W for 30 min
    assert acc.sample(0, t0 + timedelta(hours=1, minutes=30)) == pytest.approx(
        1.25
    )  # zero elapsed is harmless
    assert acc.sample(0, t0 + timedelta(hours=1)) == pytest.approx(1.25)  # clock going backwards ignored


async def test_estimated_sensors_without_cloud(
    hass: HomeAssistant, mock_unit: MagicMock, status: dict[str, Any], freezer: FrozenDateTimeFactory
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Office AC",
        data={**ENTRY_DATA, CONF_CAPABILITIES: {"capacity": 50}},
        options={OPT_FAN_POWER: 40},
        unique_id=THING,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    power = hass.states.get("sensor.office_ac_estimated_power")
    assert power.state == "1600.0"  # capacity 50 -> 1600 W, unit is on and cooling
    assert power.attributes["compressor_source"] == "mode"
    assert power.attributes["rated_power_w"] == 1600
    assert hass.states.get("sensor.office_ac_estimated_energy").state == "0.0"

    # one hour at 1600 W -> 1.6 kWh credited at the next poll
    freezer.tick(timedelta(hours=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.office_ac_estimated_energy").state) == pytest.approx(1.6, abs=0.02)

    # turn off: power drops to 0, energy stops growing
    await hass.services.async_call(
        "climate", "turn_off", {ATTR_ENTITY_ID: "climate.office_ac"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.office_ac_estimated_power").state == "0.0"
    before = float(hass.states.get("sensor.office_ac_estimated_energy").state)
    freezer.tick(timedelta(hours=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.office_ac_estimated_energy").state) == pytest.approx(
        before, abs=0.001
    )


async def test_estimated_power_uses_cloud_compressor_state(
    hass: HomeAssistant, mock_unit: MagicMock, status: dict[str, Any], mock_cloud_api: MagicMock
) -> None:
    mock_cloud_api.async_get_shadows.return_value = {THING: {**SHADOW, "Sta_CpOnOff": 0}}
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Office AC",
        data=CLOUD_DATA,
        options={**CLOUD_OPTIONS, OPT_RATED_POWER: 900},
        unique_id=THING,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    power = hass.states.get("sensor.office_ac_estimated_power")
    assert power.state == "45.0"  # on + cooling, but the cloud says the compressor is paused
    assert power.attributes["compressor_source"] == "cloud"
    assert power.attributes["rated_power_w"] == 900


async def test_estimated_energy_restores(
    hass: HomeAssistant, mock_unit: MagicMock, status: dict[str, Any]
) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        (
            (
                State("sensor.office_ac_estimated_energy", "12.5"),
                {"native_value": 12.5, "native_unit_of_measurement": "kWh"},
            ),
        ),
    )
    entry = MockConfigEntry(domain=DOMAIN, title="Office AC", data=ENTRY_DATA, unique_id=THING)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.office_ac_estimated_energy").state) == pytest.approx(12.5)


async def test_set_temperature_with_hvac_mode(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_unit: MagicMock
) -> None:
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {ATTR_ENTITY_ID: "climate.office_ac", ATTR_TEMPERATURE: 24, ATTR_HVAC_MODE: HVACMode.DRY},
        blocking=True,
    )
    mock_unit.async_set_status.assert_awaited_with({"Set_OnOff": 1, "Set_Mode": 4, "Set_Temp": 24})
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {ATTR_ENTITY_ID: "climate.office_ac", ATTR_TEMPERATURE: 25, ATTR_HVAC_MODE: HVACMode.OFF},
        blocking=True,
    )
    mock_unit.async_set_status.assert_awaited_with({"Set_OnOff": 0, "Set_Temp": 25})
