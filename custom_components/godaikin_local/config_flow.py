"""Config flow: discover units on the LAN, then ask for the per-unit key."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT

from .const import CONF_FIVE_SPEED, CONF_THING_NAME, CONF_UNIT_KEY, DEFAULT_PORT, DOMAIN
from .protocol import DaikinKeyError, DaikinLocalError, DaikinLocalUnit, async_discover

_LOGGER = logging.getLogger(__name__)


def _schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    d = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=d.get(CONF_HOST, "")): str,
            vol.Required(CONF_THING_NAME, default=d.get(CONF_THING_NAME, "")): str,
            vol.Required(CONF_UNIT_KEY): str,
            vol.Optional(CONF_NAME, default=d.get(CONF_NAME, "Daikin")): str,
            vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
            vol.Optional(CONF_FIVE_SPEED, default=False): bool,
        }
    )


class GoDaikinLocalConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, str] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None:
            try:
                units = await async_discover()
            except OSError as err:
                _LOGGER.warning("UDP discovery failed: %s", err)
                units = []
            self._discovered = {u.thing_name: u.host for u in units}
            if self._discovered:
                return await self.async_step_pick()
            return await self.async_step_manual()
        return await self.async_step_manual(user_input)

    async def async_step_pick(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            thing = user_input[CONF_THING_NAME]
            if thing == "__manual__":
                return await self.async_step_manual()
            return await self.async_step_manual(
                defaults={CONF_THING_NAME: thing, CONF_HOST: self._discovered[thing], CONF_NAME: thing}
            )
        options = {t: f"{t} ({h})" for t, h in self._discovered.items()}
        options["__manual__"] = "Enter manually"
        return self.async_show_form(
            step_id="pick",
            data_schema=vol.Schema({vol.Required(CONF_THING_NAME): vol.In(options)}),
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None, defaults: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_THING_NAME])
            self._abort_if_unique_id_configured()
            try:
                unit = DaikinLocalUnit(
                    user_input[CONF_HOST], user_input[CONF_THING_NAME], user_input[CONF_UNIT_KEY], user_input[CONF_PORT]
                )
                await unit.async_get_status()
            except DaikinKeyError:
                errors["base"] = "invalid_key"
            except (DaikinLocalError, OSError, TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)
        return self.async_show_form(step_id="manual", data_schema=_schema(defaults or user_input), errors=errors)
