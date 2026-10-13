"""Config flow tests: menu, cloud login, manual, DHCP discovery, reauth, reconfigure."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.godaikin.cloud import CloudAuthError, CloudError, CloudUnit
from custom_components.godaikin.const import (
    CONF_CAPABILITIES,
    CONF_FIVE_SPEED,
    CONF_THING_NAME,
    CONF_UNIT_KEY,
    DOMAIN,
)
from custom_components.godaikin.protocol import DaikinKeyError, DaikinLocalError, DiscoveredUnit

from .conftest import ENTRY_DATA, HOST, KEY, THING

CREDS = {CONF_USERNAME: "me@example.com", CONF_PASSWORD: "secret"}
HOST_INPUT = {CONF_HOST: HOST, CONF_NAME: "Office AC", CONF_PORT: 7002, CONF_FIVE_SPEED: False}


async def _start(hass: HomeAssistant) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["cloud", "manual"]
    return result


@pytest.mark.usefixtures("mock_unit", "mock_discovery")
async def test_cloud_flow_creates_entry(hass: HomeAssistant, mock_cloud: MagicMock) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "cloud"})
    assert result["step_id"] == "cloud"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pick"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_THING_NAME: THING})
    assert result["step_id"] == "host"
    # host is pre-filled from discovery
    assert result["data_schema"]({})[CONF_HOST] == HOST

    result = await hass.config_entries.flow.async_configure(result["flow_id"], HOST_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Office AC"
    assert {k: v for k, v in result["data"].items() if k != CONF_CAPABILITIES} == ENTRY_DATA
    assert result["data"][CONF_CAPABILITIES]["hvac_modes"]
    mock_cloud.async_login.assert_awaited_once_with("me@example.com", "secret")


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [(CloudAuthError("bad"), "invalid_auth"), (CloudError("down"), "cannot_connect")],
)
@pytest.mark.usefixtures("mock_unit", "mock_discovery")
async def test_cloud_login_errors(
    hass: HomeAssistant, mock_cloud: MagicMock, side_effect: Exception, error: str
) -> None:
    mock_cloud.async_login.side_effect = side_effect
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "cloud"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    # recovery: fix the problem and continue
    mock_cloud.async_login.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["step_id"] == "pick"


@pytest.mark.usefixtures("mock_unit", "mock_discovery")
async def test_cloud_no_units(hass: HomeAssistant, mock_cloud: MagicMock) -> None:
    mock_cloud.async_get_units.return_value = []
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "cloud"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["errors"] == {"base": "no_units"}


@pytest.mark.usefixtures("mock_unit", "mock_cloud", "mock_discovery")
async def test_cloud_all_configured(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "cloud"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "all_configured"


@pytest.mark.usefixtures("mock_cloud")
async def test_cloud_discovery_unavailable_still_works(hass: HomeAssistant, mock_unit: MagicMock) -> None:
    """When UDP discovery cannot bind, the host field is simply empty."""
    with patch(
        "custom_components.godaikin.config_flow.async_discover",
        AsyncMock(side_effect=OSError("no socket")),
    ):
        result = await _start(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "cloud"})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_THING_NAME: THING})
        assert result["step_id"] == "host"
        assert result["data_schema"]({})[CONF_HOST] == ""


@pytest.mark.usefixtures("mock_discovery")
async def test_manual_flow_and_validation_errors(hass: HomeAssistant, mock_unit: MagicMock) -> None:
    manual = {**ENTRY_DATA}
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "manual"})
    assert result["step_id"] == "manual"

    mock_unit.async_get_status.side_effect = DaikinKeyError("nope")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], manual)
    assert result["errors"] == {"base": "invalid_key"}

    mock_unit.async_get_status.side_effect = DaikinLocalError("no data")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], manual)
    assert result["errors"] == {"base": "cannot_connect"}

    mock_unit.async_get_status.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], manual)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_UNIT_KEY] == KEY


@pytest.mark.usefixtures("mock_unit", "mock_discovery")
async def test_manual_duplicate_aborts(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "manual"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {**ENTRY_DATA})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


def _dhcp(ip: str = HOST) -> DhcpServiceInfo:
    return DhcpServiceInfo(ip=ip, hostname="", macaddress="a8169d72d9d0")


@pytest.mark.usefixtures("mock_unit", "mock_cloud", "mock_discovery")
async def test_dhcp_discovery_flow(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_DHCP}, data=_dhcp()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "dhcp_confirm"
    assert result["description_placeholders"] == {"thing_name": THING, "host": HOST}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "cloud"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["step_id"] == "host"  # pick step is skipped: the unit is known
    result = await hass.config_entries.flow.async_configure(result["flow_id"], HOST_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_THING_NAME] == THING


@pytest.mark.usefixtures("mock_unit", "mock_discovery")
async def test_dhcp_unit_not_in_account(hass: HomeAssistant, mock_cloud: MagicMock) -> None:
    mock_cloud.async_get_units.return_value = [
        type("U", (), {"thing_name": "Daikin_other", "key": KEY, "name": "x"})()
    ]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_DHCP}, data=_dhcp()
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["errors"] == {"base": "unit_not_in_account"}


async def test_dhcp_ignores_non_daikin_device(hass: HomeAssistant) -> None:
    with patch("custom_components.godaikin.config_flow.async_discover", AsyncMock(return_value=[])):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_DHCP}, data=_dhcp()
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_godaikin_device"


@pytest.mark.usefixtures("mock_unit", "mock_discovery")
async def test_dhcp_updates_host_of_configured_unit(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    with patch(
        "custom_components.godaikin.config_flow.async_discover",
        AsyncMock(return_value=[DiscoveredUnit(thing_name=THING, host="192.0.2.99", raw={})]),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_DHCP}, data=_dhcp("192.0.2.99")
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert config_entry.data[CONF_HOST] == "192.0.2.99"


@pytest.mark.usefixtures("mock_unit", "mock_discovery")
async def test_reauth_flow(hass: HomeAssistant, config_entry: MockConfigEntry, mock_cloud: MagicMock) -> None:
    config_entry.async_start_reauth(hass)
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    flow_id = flows[0]["flow_id"]
    assert flows[0]["step_id"] == "reauth_confirm"

    mock_cloud.async_login.side_effect = CloudAuthError("bad")
    result = await hass.config_entries.flow.async_configure(flow_id, CREDS)
    assert result["errors"] == {"base": "invalid_auth"}
    mock_cloud.async_login.side_effect = CloudError("down")
    result = await hass.config_entries.flow.async_configure(flow_id, CREDS)
    assert result["errors"] == {"base": "cannot_connect"}

    mock_cloud.async_login.side_effect = None
    mock_cloud.async_get_units.return_value = [CloudUnit(THING, "ff" * 16, "x", {"Bar_HeatM": 1})]
    result = await hass.config_entries.flow.async_configure(flow_id, CREDS)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_UNIT_KEY] == "ff" * 16
    assert "heat" not in config_entry.data[CONF_CAPABILITIES]["hvac_modes"]


@pytest.mark.usefixtures("mock_unit", "mock_discovery")
async def test_reauth_unit_missing(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_cloud: MagicMock
) -> None:
    mock_cloud.async_get_units.return_value = []
    config_entry.async_start_reauth(hass)
    await hass.async_block_till_done()
    flow_id = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]["flow_id"]
    result = await hass.config_entries.flow.async_configure(flow_id, CREDS)
    assert result["errors"] == {"base": "unit_not_in_account"}


async def test_reconfigure_flow(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_unit: MagicMock
) -> None:
    result = await setup_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    assert result["data_schema"]({})[CONF_HOST] == HOST

    mock_unit.async_get_status.side_effect = DaikinLocalError("no data")
    new = {CONF_HOST: "192.0.2.20", CONF_NAME: "Study AC", CONF_PORT: 7002, CONF_FIVE_SPEED: True}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], new)
    assert result["errors"] == {"base": "cannot_connect"}

    mock_unit.async_get_status.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], new)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert setup_entry.data[CONF_HOST] == "192.0.2.20"
    assert setup_entry.data[CONF_FIVE_SPEED] is True
    assert setup_entry.title == "Study AC"
