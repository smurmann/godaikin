"""Config flow for GO DAIKIN Local.

Flow
----
user ──► cloud (email + password) ──► pick (units on the account) ──► host ──► entry
     └─► manual (host, thing name, key) ─────────────────────────────────────► entry

dhcp   ──► (unicast LAN probe confirms it is a GO DAIKIN unit) ──► cloud ──► host (prefilled)
reauth ──► reauth_confirm (email + password) ──► entry updated with the fresh key
reconfigure ──► host / port / name changed, key re-validated
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .capabilities import resolve
from .cloud import CloudAuthError, CloudError, CloudSession, CloudUnit, GoDaikinCloud
from .const import (
    ALL_MODES,
    ALL_PRESETS,
    CONF_CAPABILITIES,
    CONF_CLOUD_REFRESH_TOKEN,
    CONF_CLOUD_SUBJECT,
    CONF_FIVE_SPEED,
    CONF_THING_NAME,
    CONF_UNIT_KEY,
    DEFAULT_CLOUD_INTERVAL,
    DEFAULT_PORT,
    DOMAIN,
    FAN_SPEED_CHOICES,
    MAX_CLOUD_INTERVAL,
    MIN_CLOUD_INTERVAL,
    OPT_CLOUD,
    OPT_CLOUD_INTERVAL,
    OPT_FAN_SPEEDS,
    OPT_HVAC_MODES,
    OPT_PRESETS,
    OPT_SWING,
)
from .protocol import DaikinKeyError, DaikinLocalError, DaikinLocalUnit, async_discover

_LOGGER = logging.getLogger(__name__)

STEP_CLOUD_SCHEMA = vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str})
DISCOVERY_LISTEN_SECONDS = 3.0


def _host_schema(default_host: str, default_name: str, default_port: int = DEFAULT_PORT) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=default_host): str,
            vol.Optional(CONF_NAME, default=default_name): str,
            vol.Optional(CONF_PORT, default=default_port): int,
            vol.Optional(CONF_FIVE_SPEED, default=False): bool,
        }
    )


class GoDaikinLocalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI setup, discovery, reauth and reconfigure flows."""

    VERSION = 1
    MINOR_VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> GoDaikinOptionsFlow:
        return GoDaikinOptionsFlow()

    def __init__(self) -> None:
        self._units: dict[str, CloudUnit] = {}
        self._discovered: dict[str, str] = {}
        self._chosen: CloudUnit | None = None
        self._dhcp_thing: str | None = None
        self._session: CloudSession | None = None

    # ----- entry points -------------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["cloud", "manual"])

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> ConfigFlowResult:
        """A device with the Wi-Fi module's OUI appeared; confirm it is a GO DAIKIN unit."""
        host = discovery_info.ip
        try:
            units = await async_discover(targets=[host], listen_for=DISCOVERY_LISTEN_SECONDS)
        except OSError:
            units = []
        found = next((u for u in units if u.host == host), None)
        if found is None:
            return self.async_abort(reason="not_godaikin_device")
        await self.async_set_unique_id(found.thing_name)
        # discovery-update-info: keep the stored address current if the unit moved.
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        self._dhcp_thing = found.thing_name
        self._discovered[found.thing_name] = host
        self.context["title_placeholders"] = {"name": found.thing_name}
        return await self.async_step_dhcp_confirm()

    async def async_step_dhcp_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return await self.async_step_cloud()
        thing = self._dhcp_thing or ""
        return self.async_show_form(
            step_id="dhcp_confirm",
            description_placeholders={"thing_name": thing, "host": self._discovered.get(thing, "")},
        )

    # ----- cloud login --------------------------------------------------------------

    async def _fetch_units(self, username: str, password: str) -> dict[str, CloudUnit]:
        cloud = GoDaikinCloud(async_get_clientsession(self.hass))
        self._session = await cloud.async_login(username, password)
        return {u.thing_name: u for u in await cloud.async_get_units(self._session) if u.key}

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
                elif self._dhcp_thing is None:
                    return await self.async_step_pick()
                elif self._dhcp_thing not in self._units:
                    errors["base"] = "unit_not_in_account"
                else:
                    self._chosen = self._units[self._dhcp_thing]
                    return await self.async_step_host()
        return self.async_show_form(step_id="cloud", data_schema=STEP_CLOUD_SCHEMA, errors=errors)

    async def async_step_pick(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._chosen = self._units[user_input[CONF_THING_NAME]]
            await self.async_set_unique_id(self._chosen.thing_name)
            self._abort_if_unique_id_configured()
            return await self.async_step_host()
        if not self._discovered:
            try:
                found = await async_discover(listen_for=DISCOVERY_LISTEN_SECONDS)
            except OSError as err:
                _LOGGER.debug("UDP discovery unavailable: %s", err)
                found = []
            self._discovered = {u.thing_name: u.host for u in found}
        configured = {entry.unique_id for entry in self._async_current_entries()}
        options = {
            thing: (unit.name or thing)
            + (f" ({self._discovered[thing]})" if thing in self._discovered else "")
            for thing, unit in self._units.items()
            if thing not in configured
        }
        if not options:
            return self.async_abort(reason="all_configured")
        return self.async_show_form(
            step_id="pick", data_schema=vol.Schema({vol.Required(CONF_THING_NAME): vol.In(options)})
        )

    async def async_step_host(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._chosen is None:  # pragma: no cover - defensive
            return self.async_abort(reason="unknown")
        thing = self._chosen.thing_name
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                CONF_HOST: user_input[CONF_HOST],
                CONF_PORT: user_input[CONF_PORT],
                CONF_THING_NAME: thing,
                CONF_UNIT_KEY: self._chosen.key,
                CONF_NAME: user_input[CONF_NAME],
                CONF_FIVE_SPEED: user_input[CONF_FIVE_SPEED],
                CONF_CAPABILITIES: self._chosen.capabilities,
            }
            errors = await self._validate_local(data)
            if not errors:
                return self.async_create_entry(title=user_input[CONF_NAME], data=data)
        return self.async_show_form(
            step_id="host",
            data_schema=_host_schema(self._discovered.get(thing, ""), self._chosen.name or thing),
            errors=errors,
            description_placeholders={"thing_name": thing},
        )

    # ----- manual -------------------------------------------------------------------

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

    # ----- reauth (key rejected) ----------------------------------------------------

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
                    updates: dict[str, Any] = {CONF_UNIT_KEY: unit.key, CONF_CAPABILITIES: unit.capabilities}
                    if (
                        entry.data.get(CONF_CLOUD_REFRESH_TOKEN)
                        and self._session
                        and self._session.refresh_token
                    ):
                        updates[CONF_CLOUD_REFRESH_TOKEN] = self._session.refresh_token
                        updates[CONF_CLOUD_SUBJECT] = self._session.subject
                    return self.async_update_reload_and_abort(entry, data_updates=updates)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_CLOUD_SCHEMA,
            errors=errors,
            description_placeholders={"thing_name": entry.data[CONF_THING_NAME]},
        )

    # ----- reconfigure (address / name changed) -------------------------------------

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._validate_local({**entry.data, **user_input})
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates=user_input, title=user_input[CONF_NAME]
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_host_schema(
                entry.data[CONF_HOST], entry.title, entry.data.get(CONF_PORT, DEFAULT_PORT)
            ),
            errors=errors,
            description_placeholders={"thing_name": entry.data[CONF_THING_NAME]},
        )

    # ----- helpers ------------------------------------------------------------------

    async def _validate_local(self, data: Mapping[str, Any]) -> dict[str, str]:
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


class GoDaikinOptionsFlow(OptionsFlow):
    """Override the detected feature set and opt in to cloud sensors."""

    def __init__(self) -> None:
        self._pending: dict[str, Any] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._pending = user_input
            needs_login = user_input.get(OPT_CLOUD) and not self.config_entry.data.get(
                CONF_CLOUD_REFRESH_TOKEN
            )
            if needs_login:
                return await self.async_step_cloud_login()
            return self.async_create_entry(data=user_input)
        caps = resolve(self.config_entry)
        opts = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(OPT_HVAC_MODES, default=list(caps.hvac_modes)): cv.multi_select(
                    {m: m for m in ALL_MODES}
                ),
                vol.Required(OPT_PRESETS, default=list(caps.presets)): cv.multi_select(
                    {p: p for p in ALL_PRESETS}
                ),
                vol.Required(OPT_FAN_SPEEDS, default=str(caps.fan_speeds)): vol.In(FAN_SPEED_CHOICES),
                vol.Required(OPT_SWING, default=caps.swing): bool,
                vol.Required(OPT_CLOUD, default=bool(opts.get(OPT_CLOUD, False))): bool,
                vol.Required(
                    OPT_CLOUD_INTERVAL, default=int(opts.get(OPT_CLOUD_INTERVAL, DEFAULT_CLOUD_INTERVAL))
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_CLOUD_INTERVAL, max=MAX_CLOUD_INTERVAL)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_cloud_login(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """One-time sign-in to obtain a refresh token; the password is used here and discarded."""
        errors: dict[str, str] = {}
        if user_input is not None:
            cloud = GoDaikinCloud(async_get_clientsession(self.hass))
            try:
                session = await cloud.async_login(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
                units = await cloud.async_get_units(session)
            except CloudAuthError:
                errors["base"] = "invalid_auth"
            except CloudError:
                errors["base"] = "cannot_connect"
            else:
                thing = self.config_entry.data[CONF_THING_NAME]
                if not session.refresh_token or not session.subject:
                    errors["base"] = "cannot_connect"
                elif thing not in {u.thing_name for u in units}:
                    errors["base"] = "unit_not_in_account"
                else:
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        data={
                            **self.config_entry.data,
                            CONF_CLOUD_REFRESH_TOKEN: session.refresh_token,
                            CONF_CLOUD_SUBJECT: session.subject,
                        },
                    )
                    return self.async_create_entry(data=self._pending)
        return self.async_show_form(step_id="cloud_login", data_schema=STEP_CLOUD_SCHEMA, errors=errors)
