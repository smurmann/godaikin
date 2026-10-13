"""Shared fixtures: a fake unit on the LAN and a fake GO DAIKIN cloud."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.godaikin.cloud import CloudSession, CloudUnit
from custom_components.godaikin.const import CONF_FIVE_SPEED, CONF_THING_NAME, CONF_UNIT_KEY, DOMAIN
from custom_components.godaikin.protocol import DiscoveredUnit

THING = "Daikin_aabbccddeeff"
KEY = "00112233445566778899aabbccddeeff"
HOST = "192.0.2.10"

STATUS: dict[str, Any] = {
    "updatedOn": "2026-10-1212:05:04",
    "thingName": THING,
    "version": "V1.2.3",
    "eventType": "connected",
    "Set_OnOff": 1,
    "Set_Fan": 4,
    "Set_Mode": 1,
    "Set_Temp": 21,
    "Set_Turbo": 0,
    "Set_Silent": 0,
    "Set_Sleep": 0,
    "Set_Ion": 1,
    "Set_Swing": 0,
    "Set_UDLvr": 3,
}

ENTRY_DATA = {
    CONF_HOST: HOST,
    CONF_PORT: 7002,
    CONF_THING_NAME: THING,
    CONF_UNIT_KEY: KEY,
    CONF_NAME: "Office AC",
    CONF_FIVE_SPEED: False,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let the test harness load custom_components/."""


@pytest.fixture
def status() -> dict[str, Any]:
    return dict(STATUS)


@pytest.fixture
def mock_unit(status: dict[str, Any]) -> Generator[MagicMock]:
    """Replace DaikinLocalUnit everywhere it is constructed."""
    unit = MagicMock()
    unit.host = HOST
    unit.port = 7002
    unit.thing_name = THING
    unit.async_get_status = AsyncMock(return_value=status)

    async def _apply(state: dict[str, Any]) -> dict[str, Any]:
        """The real unit echoes and applies the fields it was sent."""
        status.update(state)
        return dict(state)

    unit.async_set_status = AsyncMock(side_effect=_apply)
    with (
        patch("custom_components.godaikin.DaikinLocalUnit", return_value=unit),
        patch("custom_components.godaikin.config_flow.DaikinLocalUnit", return_value=unit),
    ):
        yield unit


@pytest.fixture
def mock_discovery() -> Generator[AsyncMock]:
    with patch(
        "custom_components.godaikin.config_flow.async_discover",
        AsyncMock(return_value=[DiscoveredUnit(thing_name=THING, host=HOST, raw={})]),
    ) as discover:
        yield discover


@pytest.fixture
def mock_cloud() -> Generator[MagicMock]:
    cloud = MagicMock()
    cloud.async_login = AsyncMock(return_value=MagicMock(user_id="u1", region="DM"))
    cloud.async_get_units = AsyncMock(
        return_value=[
            CloudUnit(thing_name=THING, key=KEY, name="Office AC", raw={}),
            CloudUnit(thing_name="Daikin_000000000000", key=None, name="Keyless", raw={}),
        ]
    )
    with patch("custom_components.godaikin.config_flow.GoDaikinCloud", return_value=cloud):
        yield cloud


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Office AC", data=ENTRY_DATA, unique_id=THING)
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def setup_entry(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_unit: MagicMock
) -> AsyncGenerator[MockConfigEntry]:
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    yield config_entry


SUBJECT = "6417645e-0000-4984-bdfa-4caf96c7e49b"


def cloud_session(**over: Any) -> CloudSession:
    base = {
        "id_token": "t",
        "access_token": "a",
        "user_id": "u1",
        "region": "DM",
        "username": SUBJECT,
        "expires_at": 1e18,  # never expires unless a test overrides it (freezegun shifts monotonic)
        "refresh_token": "rt-1",
        "subject": SUBJECT,
    }
    return CloudSession(**{**base, **over})


CLOUD_SHADOW: dict[str, Any] = {
    "thingName": THING,
    "eventType": "connected",
    "Sta_IDRoomTemp": 30,
    "Sta_IDCoilTemp": 26,
    "Sta_ODAirTemp": 0,
    "Sta_ODCoilTemp": -30,
    "Sta_ODDiscTemp": 0,
    "Sta_ODCpFreq": 0,
    "Sta_ODPwrCon": 0,
    "Sta_IDRh": 0,
    "Sta_DCBus": 325,
    "Sta_CpRT": 12,
    "Sta_ErrCode": 0,
    "Sta_CpOnOff": 1,
    "Sta_HumanDct": 0,
    "Set_OnOff": 1,
}


@pytest.fixture
def mock_cloud_api() -> Generator[MagicMock]:
    """Fake GoDaikinCloud for the cloud coordinator and the options/reauth flows."""
    api = MagicMock()
    api.async_refresh = AsyncMock(return_value=cloud_session())
    api.async_login = AsyncMock(return_value=cloud_session())
    api.async_get_shadows = AsyncMock(return_value={THING: dict(CLOUD_SHADOW)})
    api.async_get_units = AsyncMock(return_value=[MagicMock(thing_name=THING, key="k")])
    with (
        patch("custom_components.godaikin.cloud_coordinator.GoDaikinCloud", return_value=api),
        patch("custom_components.godaikin.config_flow.GoDaikinCloud", return_value=api),
    ):
        yield api
