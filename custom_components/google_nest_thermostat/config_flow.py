"""Config flow and reauthentication for a polling-only SDM connection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    NestApiClient,
    NestAuthenticationError,
    NestConnectionError,
    NestError,
    NestPermissionError,
    NestRateLimitError,
    normalize_project_id,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_ID,
    CONF_EXPIRES_AT,
    CONF_FAN_DURATION,
    CONF_PROJECT_ID,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_FAN_DURATION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .models import NestData, NestTokens

PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


@dataclass(frozen=True, slots=True)
class ValidatedConnection:
    data: NestData
    tokens: NestTokens


def connection_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_PROJECT_ID): str,
            vol.Required(CONF_CLIENT_ID): str,
            vol.Required(CONF_CLIENT_SECRET): PASSWORD,
            vol.Required(CONF_REFRESH_TOKEN): PASSWORD,
        }
    )


def _flow_error(err: Exception) -> str:
    if isinstance(err, ValueError):
        return "invalid_project"
    if isinstance(err, NestAuthenticationError):
        return "invalid_auth"
    if isinstance(err, NestPermissionError):
        return "forbidden"
    if isinstance(err, NestRateLimitError):
        return "rate_limited"
    if isinstance(err, NestConnectionError):
        return "cannot_connect"
    return "unknown"


class NestThermostatConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _validate(self, user_input: dict[str, Any]) -> ValidatedConnection:
        client = NestApiClient(
            async_get_clientsession(self.hass),
            project_id=normalize_project_id(user_input[CONF_PROJECT_ID]),
            client_id=user_input[CONF_CLIENT_ID],
            client_secret=user_input[CONF_CLIENT_SECRET],
            refresh_token=user_input[CONF_REFRESH_TOKEN],
        )
        data = await client.async_get_devices()
        if data.account_id is None:
            raise NestAuthenticationError(
                "Google did not return a linked-user identity"
            )
        return ValidatedConnection(data, client.tokens)

    @staticmethod
    def _entry_data(
        user_input: dict[str, Any], result: ValidatedConnection
    ) -> dict[str, Any]:
        return {
            CONF_PROJECT_ID: normalize_project_id(user_input[CONF_PROJECT_ID]),
            CONF_CLIENT_ID: user_input[CONF_CLIENT_ID].strip(),
            CONF_CLIENT_SECRET: user_input[CONF_CLIENT_SECRET].strip(),
            CONF_REFRESH_TOKEN: result.tokens.refresh_token,
            CONF_ACCESS_TOKEN: result.tokens.access_token,
            CONF_EXPIRES_AT: result.tokens.expires_at,
            CONF_ACCOUNT_ID: result.data.account_id,
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                result = await self._validate(user_input)
            except (NestError, ValueError) as err:
                errors["base"] = _flow_error(err)
            else:
                if not result.data.thermostats:
                    errors["base"] = "no_thermostats"
                else:
                    project = normalize_project_id(user_input[CONF_PROJECT_ID])
                    await self.async_set_unique_id(
                        f"{project}:{result.data.account_id}"
                    )
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title="Google Nest Thermostat",
                        data=self._entry_data(user_input, result),
                    )
        return self.async_show_form(
            step_id="user", data_schema=connection_schema(), errors=errors
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            credentials = {**entry.data, **user_input}
            if not user_input.get(CONF_CLIENT_SECRET):
                credentials[CONF_CLIENT_SECRET] = entry.data[CONF_CLIENT_SECRET]
            try:
                result = await self._validate(credentials)
            except (NestError, ValueError) as err:
                errors["base"] = _flow_error(err)
            else:
                if result.data.account_id != entry.data[CONF_ACCOUNT_ID]:
                    errors["base"] = "wrong_account"
                else:
                    return self.async_update_reload_and_abort(
                        entry, data_updates=self._entry_data(credentials, result)
                    )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_REFRESH_TOKEN): PASSWORD,
                    vol.Optional(CONF_CLIENT_SECRET): PASSWORD,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> NestOptionsFlow:
        return NestOptionsFlow()


class NestOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                ),
                vol.Required(
                    CONF_FAN_DURATION,
                    default=self.config_entry.options.get(
                        CONF_FAN_DURATION, DEFAULT_FAN_DURATION
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=720)),
            }
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                validated = schema(user_input)
            except vol.Invalid:
                errors["base"] = "invalid_options"
            else:
                return self.async_create_entry(title="", data=validated)
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
