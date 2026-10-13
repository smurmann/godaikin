"""Optional cloud sensors: refresh-token sessions, coordinator recovery, entities, options flow."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Generator
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.godaikin_local import cloud as cloud_mod
from custom_components.godaikin_local.cloud import (
    CloudAuthError,
    CloudError,
    CloudSession,
    CloudUnit,
    GoDaikinCloud,
)
from custom_components.godaikin_local.const import (
    CONF_CLOUD_REFRESH_TOKEN,
    CONF_CLOUD_SUBJECT,
    DOMAIN,
    OPT_CLOUD,
    OPT_CLOUD_INTERVAL,
    OPT_FAN_SPEEDS,
    OPT_HVAC_MODES,
    OPT_PRESETS,
    OPT_SWING,
)
from custom_components.godaikin_local.diagnostics import async_get_config_entry_diagnostics

from .conftest import ENTRY_DATA, THING

SUBJECT = "6417645e-0000-4984-bdfa-4caf96c7e49b"
CLOUD_DATA = {**ENTRY_DATA, CONF_CLOUD_REFRESH_TOKEN: "rt-1", CONF_CLOUD_SUBJECT: SUBJECT}
CLOUD_OPTIONS = {OPT_CLOUD: True, OPT_CLOUD_INTERVAL: 10}

# Shadow as the live units report it (subset).
SHADOW: dict[str, Any] = {
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


def _jwt(claims: dict[str, Any]) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"h.{payload}.s"


def _session(**over: Any) -> CloudSession:
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


@pytest.fixture
def mock_cloud_api() -> Generator[MagicMock]:
    api = MagicMock()
    api.async_refresh = AsyncMock(return_value=_session())
    api.async_login = AsyncMock(return_value=_session())
    api.async_get_shadows = AsyncMock(return_value={THING: dict(SHADOW)})
    api.async_get_units = AsyncMock(return_value=[MagicMock(thing_name=THING, key="k")])
    with (
        patch("custom_components.godaikin_local.cloud_coordinator.GoDaikinCloud", return_value=api),
        patch("custom_components.godaikin_local.config_flow.GoDaikinCloud", return_value=api),
    ):
        yield api


@pytest.fixture
async def cloud_entry(
    hass: HomeAssistant, mock_unit: MagicMock, mock_cloud_api: MagicMock
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Office AC", data=CLOUD_DATA, options=CLOUD_OPTIONS, unique_id=THING
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


# ----- cloud client -----------------------------------------------------------------


def test_jwt_claims() -> None:
    assert cloud_mod.jwt_claims(_jwt({"sub": "x"})) == {"sub": "x"}
    assert cloud_mod.jwt_claims("garbage") == {}
    assert cloud_mod.jwt_claims("a.!!.c") == {}


async def test_login_and_refresh_parse(hass: HomeAssistant) -> None:
    api = GoDaikinCloud(MagicMock())
    login_doc = {
        "IdToken": _jwt({"sub": SUBJECT}),
        "AccessToken": "acc",
        "RefreshToken": "rt-1",
        "ExpiresIn": 28800,
        "userID": "u1",
        "activeRegion": "MY",
    }
    with patch.object(api, "_post", AsyncMock(return_value=login_doc)) as post:
        session = await api.async_login("me@example.com", "pw")
        assert session.subject == SUBJECT and session.refresh_token == "rt-1" and session.region == "DM"
        assert not session.expired()
        assert post.await_args.args[1] == {"requestData": {"username": "me@example.com", "password": "pw"}}

    refresh_doc = {
        "IdToken": _jwt({"sub": SUBJECT}),
        "AccessToken": "acc2",
        "ExpiresIn": "bad",
        "userID": "u1",
    }
    with patch.object(api, "_post", AsyncMock(return_value=refresh_doc)) as post:
        session = await api.async_refresh(SUBJECT, "rt-1")
        assert (
            session.access_token == "acc2" and session.refresh_token == "rt-1" and session.subject == SUBJECT
        )
        # verified live: type 1 + Cognito subject as username
        assert post.await_args.args[1] == {
            "requestData": {"type": 1, "username": SUBJECT, "refreshToken": "rt-1"}
        }

    with (
        patch.object(api, "_post", AsyncMock(return_value={"message": "nope"})),
        pytest.raises(CloudAuthError),
    ):
        await api.async_refresh(SUBJECT, "rt-1")
    with (
        patch.object(api, "_post", AsyncMock(return_value={"message": "nope"})),
        pytest.raises(CloudAuthError),
    ):
        await api.async_login("me@example.com", "pw")

    with patch.object(
        api, "async_get_units", AsyncMock(return_value=[MagicMock(thing_name=THING, raw={"a": 1})])
    ):
        assert await api.async_get_shadows(_session()) == {THING: {"a": 1}}

    assert _session(expires_at=time.monotonic() + 10).expired()  # inside the 5-minute margin


# ----- coordinator + entities -------------------------------------------------------


async def test_cloud_entities_and_climate_temperature(
    hass: HomeAssistant, cloud_entry: MockConfigEntry
) -> None:
    assert cloud_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.office_ac_room_temperature").state == "30.0"
    assert hass.states.get("sensor.office_ac_dc_bus_voltage").state == "325.0"
    assert hass.states.get("sensor.office_ac_compressor_runtime").state == "12.0"
    assert hass.states.get("binary_sensor.office_ac_compressor").state == "on"
    assert hass.states.get("binary_sensor.office_ac_cloud_connection").state == "on"
    assert hass.states.get("binary_sensor.office_ac_presence").state == "off"
    # local status has no room temperature; the climate entity borrows the cloud value
    assert hass.states.get("climate.office_ac").attributes["current_temperature"] == 30.0


async def test_cloud_session_reused_and_refreshed(
    hass: HomeAssistant,
    cloud_entry: MockConfigEntry,
    mock_cloud_api: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    assert mock_cloud_api.async_refresh.await_count == 1
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert mock_cloud_api.async_refresh.await_count == 1  # token still valid: no second refresh
    assert mock_cloud_api.async_get_shadows.await_count == 2

    # access token rejected once -> refresh token redeemed again, poll succeeds
    mock_cloud_api.async_get_shadows.side_effect = [
        CloudAuthError("expired"),
        {THING: {**SHADOW, "Sta_IDRoomTemp": 28}},
    ]
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert mock_cloud_api.async_refresh.await_count == 2
    assert hass.states.get("sensor.office_ac_room_temperature").state == "28.0"
    assert hass.states.get("climate.office_ac").attributes["current_temperature"] == 28.0


async def test_cloud_outage_does_not_break_local_control(
    hass: HomeAssistant,
    cloud_entry: MockConfigEntry,
    mock_cloud_api: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    mock_cloud_api.async_get_shadows.side_effect = CloudError("down")
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.office_ac_room_temperature").state == "unavailable"
    assert hass.states.get("climate.office_ac").state != "unavailable"

    # a unit that vanished from the account is an update failure, not an auth failure
    mock_cloud_api.async_get_shadows.side_effect = None
    mock_cloud_api.async_get_shadows.return_value = {}
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.office_ac_room_temperature").state == "unavailable"


async def test_cloud_refresh_token_rejected_triggers_reauth(
    hass: HomeAssistant,
    cloud_entry: MockConfigEntry,
    mock_cloud_api: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    mock_cloud_api.async_get_shadows.side_effect = CloudAuthError("expired")
    mock_cloud_api.async_refresh.side_effect = CloudAuthError("revoked")
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_setup_with_cloud_down_still_loads(
    hass: HomeAssistant, mock_unit: MagicMock, mock_cloud_api: MagicMock
) -> None:
    mock_cloud_api.async_refresh.side_effect = CloudError("down")
    entry = MockConfigEntry(
        domain=DOMAIN, title="Office AC", data=CLOUD_DATA, options=CLOUD_OPTIONS, unique_id=THING
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("climate.office_ac") is not None
    assert hass.states.get("sensor.office_ac_room_temperature") is None  # nothing known yet


async def test_cloud_disabled_without_token(
    hass: HomeAssistant, mock_unit: MagicMock, mock_cloud_api: MagicMock
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Office AC", data=ENTRY_DATA, options=CLOUD_OPTIONS, unique_id=THING
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.office_ac_compressor") is None
    mock_cloud_api.async_refresh.assert_not_awaited()


async def test_diagnostics_include_cloud(hass: HomeAssistant, cloud_entry: MockConfigEntry) -> None:
    diag = await async_get_config_entry_diagnostics(hass, cloud_entry)
    assert diag["entry"][CONF_CLOUD_REFRESH_TOKEN] == "**REDACTED**"
    assert diag["cloud"]["Sta_IDRoomTemp"] == 30


# ----- options flow: enabling cloud sensors asks for a one-time login --------------------


async def test_options_enable_cloud_requires_login(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_cloud_api: MagicMock
) -> None:
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    base = {OPT_HVAC_MODES: ["cool"], OPT_PRESETS: [], OPT_FAN_SPEEDS: "3", OPT_SWING: True}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**base, OPT_CLOUD: True, OPT_CLOUD_INTERVAL: 5}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "cloud_login"

    mock_cloud_api.async_login.side_effect = CloudAuthError("bad")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_USERNAME: "me@example.com", CONF_PASSWORD: "bad"}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    mock_cloud_api.async_login.side_effect = CloudError("down")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_USERNAME: "me@example.com", CONF_PASSWORD: "pw"}
    )
    assert result["errors"] == {"base": "cannot_connect"}

    mock_cloud_api.async_login.side_effect = None
    mock_cloud_api.async_get_units.return_value = [MagicMock(thing_name="Daikin_other")]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_USERNAME: "me@example.com", CONF_PASSWORD: "pw"}
    )
    assert result["errors"] == {"base": "unit_not_in_account"}

    mock_cloud_api.async_get_units.return_value = [MagicMock(thing_name=THING)]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_USERNAME: "me@example.com", CONF_PASSWORD: "pw"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    # refresh token + subject stored, password nowhere
    assert setup_entry.data[CONF_CLOUD_REFRESH_TOKEN] == "rt-1"
    assert setup_entry.data[CONF_CLOUD_SUBJECT] == SUBJECT
    assert "pw" not in json.dumps(dict(setup_entry.data)) and "pw" not in json.dumps(
        dict(setup_entry.options)
    )
    assert setup_entry.options[OPT_CLOUD] is True and setup_entry.options[OPT_CLOUD_INTERVAL] == 5
    assert hass.states.get("sensor.office_ac_room_temperature").state == "30.0"


async def test_options_cloud_login_without_refresh_token(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_cloud_api: MagicMock
) -> None:
    mock_cloud_api.async_login.return_value = _session(refresh_token=None)
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            OPT_HVAC_MODES: ["cool"],
            OPT_PRESETS: [],
            OPT_FAN_SPEEDS: "3",
            OPT_SWING: True,
            OPT_CLOUD: True,
            OPT_CLOUD_INTERVAL: 5,
        },
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_USERNAME: "me@example.com", CONF_PASSWORD: "pw"}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_options_cloud_already_authorised_skips_login(
    hass: HomeAssistant, cloud_entry: MockConfigEntry, mock_cloud_api: MagicMock
) -> None:
    result = await hass.config_entries.options.async_init(cloud_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            OPT_HVAC_MODES: ["cool"],
            OPT_PRESETS: [],
            OPT_FAN_SPEEDS: "5",
            OPT_SWING: True,
            OPT_CLOUD: True,
            OPT_CLOUD_INTERVAL: 10,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    mock_cloud_api.async_login.assert_not_awaited()


@pytest.mark.usefixtures("mock_unit", "mock_discovery")
async def test_reauth_renews_refresh_token_when_cloud_enabled(
    hass: HomeAssistant, mock_cloud_api: MagicMock
) -> None:
    """The main-flow reauth (key rejected) also rotates the stored refresh token."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="Office AC", data=CLOUD_DATA, options=CLOUD_OPTIONS, unique_id=THING
    )
    entry.add_to_hass(hass)
    mock_cloud_api.async_login.return_value = _session(refresh_token="rt-2")
    mock_cloud_api.async_get_units.return_value = [CloudUnit(THING, "ff" * 16, "x", {})]
    entry.async_start_reauth(hass)
    await hass.async_block_till_done()
    flow_id = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]["flow_id"]
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_USERNAME: "me@example.com", CONF_PASSWORD: "pw"}
    )
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_CLOUD_REFRESH_TOKEN] == "rt-2"


async def test_two_entries_share_one_cloud_poll(
    hass: HomeAssistant, mock_unit: MagicMock, mock_cloud_api: MagicMock
) -> None:
    """Rate-limit friendliness: a second unit on the same account reuses the account fetch."""
    other = "Daikin_000000000002"
    mock_cloud_api.async_get_shadows.return_value = {
        THING: dict(SHADOW),
        other: {**SHADOW, "thingName": other},
    }
    for title, thing in (("Office AC", THING), ("Guest AC", other)):
        entry = MockConfigEntry(
            domain=DOMAIN,
            title=title,
            data={**CLOUD_DATA, "thing_name": thing},
            options=CLOUD_OPTIONS,
            unique_id=thing,
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert mock_cloud_api.async_get_shadows.await_count == 1  # second entry served from the account cache
    assert mock_cloud_api.async_refresh.await_count == 1  # one session for the account
    assert hass.states.get("binary_sensor.guest_ac_compressor").state == "on"
    assert hass.states.get("sensor.guest_ac_room_temperature").state == "30.0"
