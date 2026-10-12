"""Config flow: log in with a GO DAIKIN account (or enter a key by hand), then pick the unit.

Flow
----
user ──► cloud (email + password) ──► pick (units from the account) ──► host ──► entry
     └─► manual (host, thing name, key) ─────────────────────────────────────────► entry

reauth ──► reauth_confirm (password) ──► entry updated with the fresh key
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .cloud import CloudAuthError, CloudError, CloudUnit, GoDaikinCloud
from .const import CONF_FIVE_SPEED, CONF_THING_NAME, CONF_UNIT_KEY, DEFAULT_PORT, DOMAIN
from .protocol import DaikinKeyError, DaikinLocalError, DaikinLocalUnit, async_discover

_LOGGER = logging.getLogger(__name__)

STEP_CLOUD_SCHEMA = vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str})


class GoDaikinLocalConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._units: dict[str, CloudUnit] = {}
        self._discovered: dict[str, str] = {}
        self._chosen: CloudUnit | None = None

    # ----- entry point -------------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["cloud", "manual"])

    # ----- cloud login -------------------------------------------------------------

    async def _fetch_units(self, username: str, password: str) -> dict[str, CloudUnit]:
        cloud = GoDaikinCloud(async_get_clientsession(self.hass))
        session = await cloud.async_login(username, password)
        units = await cloud.async_get_units(session)
        return {u.thing_name: u for u in units if u.key}

    async def async_step_cloud(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._units = await self._fetch_units(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            except CloudAuthError:
                errors["base"] = "invalid_auth"
            except CloudError as err:
                _LOGGER.warning("GO DAIKIN cloud error: %s", err)
                errors["base"] = "cannot_connect"
            else:
                if not self._units:
                    errors["base"] = "no_units"
                else:
                    return await self.async_step_pick()
        return self.async_show_form(step_id="cloud", data_schema=STEP_CLOUD_SCHEMA, errors=errors)

    async def async_step_pick(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._chosen = self._units[user_input[CONF_THING_NAME]]
            await self.async_set_unique_id(self._chosen.thing_name)
            self._abort_if_unique_id_configured()
            return await self.async_step_host()
        if not self._discovered:
            try:
                self._discovered = {u.thing_name: u.host for u in await async_discover(listen_for=3.0)}
            except OSError as err:
                _LOGGER.debug("UDP discovery unavailable: %s", err)
        configured = {e.unique_id for e in self._async_current_entries()}
        options = {
            t: f"{u.name or t}" + (f" ({self._discovered[t]})" if t in self._discovered else "")
            for t, u in self._units.items()
            if t not in configured
        }
        if not options:
            return self.async_abort(reason="all_configured")
        return self.async_show_form(
            step_id="pick", data_schema=vol.Schema({vol.Required(CONF_THING_NAME): vol.In(options)})
        )

    async def async_step_host(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._chosen is not None
        errors: dict[str, str] = {}
        thing = self._chosen.thing_name
        if user_input is not None:
            data = {
                CONF_HOST: user_input[CONF_HOST],
                CONF_PORT: user_input[CONF_PORT],
                CONF_THING_NAME: thing,
                CONF_UNIT_KEY: self._chosen.key,
                CONF_NAME: user_input[CONF_NAME],
                CONF_FIVE_SPEED: user_input[CONF_FIVE_SPEED],
            }
            errors = await self._validate_local(data)
            if not errors:
                return self.async_create_entry(title=user_input[CONF_NAME], data=data)
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=self._discovered.get(thing, "")): str,
                vol.Optional(CONF_NAME, default=self._chosen.name or thing): str,
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Optional(CONF_FIVE_SPEED, default=False): bool,
            }
        )
        return self.async_show_form(
            step_id="host", data_schema=schema, errors=errors, description_placeholders={"thing_name": thing}
        )

    # ----- manual ------------------------------------------------------------------

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_THING_NAME])
            self._abort_if_unique_id_configured()
            errors = await self._validate_local(user_input)
            if not errors:
                return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_THING_NAME): str,
                vol.Required(CONF_UNIT_KEY): str,
                vol.Optional(CONF_NAME, default="Daikin"): str,
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Optional(CONF_FIVE_SPEED, default=False): bool,
            }
        )
        return self.async_show_form(step_id="manual", data_schema=schema, errors=errors)

    # ----- reauth (key rejected) ---------------------------------------------------

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                units = await self._fetch_units(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            except CloudAuthError:
                errors["base"] = "invalid_auth"
            except CloudError:
                errors["base"] = "cannot_connect"
            else:
                unit = units.get(entry.data[CONF_THING_NAME])
                if unit is None or not unit.key:
                    errors["base"] = "unit_not_in_account"
                else:
                    return self.async_update_reload_and_abort(entry, data_updates={CONF_UNIT_KEY: unit.key})
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_CLOUD_SCHEMA,
            errors=errors,
            description_placeholders={"thing_name": entry.data[CONF_THING_NAME]},
        )

    # ----- helpers -----------------------------------------------------------------

    async def _validate_local(self, data: dict[str, Any]) -> dict[str, str]:
        try:
            unit = DaikinLocalUnit(
                data[CONF_HOST], data[CONF_THING_NAME], data[CONF_UNIT_KEY], data[CONF_PORT]
            )
            await unit.async_get_status()
        except DaikinKeyError:
            return {"base": "invalid_key"}
        except (DaikinLocalError, OSError, TimeoutError, ValueError):
            return {"base": "cannot_connect"}
        return {}

    @property
    def _is_reauth(self) -> bool:
        return self.source == SOURCE_REAUTH
