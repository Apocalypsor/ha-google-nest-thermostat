"""Validate setup, duplicate detection, reauth identity and options through HA."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType, InvalidData

from custom_components.google_nest_thermostat.api import (
    NestAuthenticationError,
    NestConnectionError,
    NestPermissionError,
    NestRateLimitError,
)
from custom_components.google_nest_thermostat.const import DOMAIN
from custom_components.google_nest_thermostat.models import NestData

from .conftest import ACCOUNT, PROJECT

INPUT = {
    "project_id": PROJECT,
    "client_id": "test.apps.googleusercontent.com",
    "client_secret": "test-client-secret",
    "refresh_token": "test-refresh",
}


async def test_user_flow_validates_and_stores_credentials(hass, client):
    with (
        patch(
            "custom_components.google_nest_thermostat.config_flow.NestApiClient",
            return_value=client,
        ),
        patch(
            "custom_components.google_nest_thermostat.async_setup_entry",
            return_value=True,
        ),
    ):
        form = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert form["type"] == FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(form["flow_id"], INPUT)
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["account_id"] == ACCOUNT
    assert result["data"]["refresh_token"] == "test-refresh"


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (NestAuthenticationError(), "invalid_auth"),
        (NestPermissionError(), "forbidden"),
        (NestConnectionError(), "cannot_connect"),
        (NestRateLimitError(), "rate_limited"),
    ],
)
async def test_user_flow_errors(hass, client, error, key):
    client.error = error
    with patch(
        "custom_components.google_nest_thermostat.config_flow.NestApiClient",
        return_value=client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}, data=INPUT
        )
    assert result["type"] == FlowResultType.FORM and result["errors"] == {"base": key}


async def test_invalid_project_never_contacts_google(hass):
    with patch(
        "custom_components.google_nest_thermostat.config_flow.NestApiClient"
    ) as client:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={**INPUT, "project_id": "cloud-project-not-dac-uuid"},
        )
    assert result["errors"] == {"base": "invalid_project"}
    client.assert_not_called()


async def test_no_thermostat_and_missing_user_identity(hass, client):
    with patch(
        "custom_components.google_nest_thermostat.config_flow.NestApiClient",
        return_value=client,
    ):
        client.data = NestData({}, ACCOUNT)
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}, data=INPUT
        )
        assert result["errors"] == {"base": "no_thermostats"}
        client.data = NestData({}, None)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], INPUT
        )
        assert result["errors"] == {"base": "invalid_auth"}


async def test_duplicate_account_project_aborted(hass, entry, client):
    with patch(
        "custom_components.google_nest_thermostat.config_flow.NestApiClient",
        return_value=client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}, data=INPUT
        )
    assert (
        result["type"] == FlowResultType.ABORT
        and result["reason"] == "already_configured"
    )


async def test_reauth_same_user_updates_and_reloads(hass, entry, client):
    with (
        patch(
            "custom_components.google_nest_thermostat.config_flow.NestApiClient",
            return_value=client,
        ),
        patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as reload,
    ):
        form = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], {"refresh_token": "replacement"}
        )
        await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert entry.data["access_token"] == client.tokens.access_token
    reload.assert_awaited_once()


async def test_reauth_wrong_account_does_not_replace_credentials(hass, entry, client):
    client.data = NestData(client.data.thermostats, "different-user")
    with patch(
        "custom_components.google_nest_thermostat.config_flow.NestApiClient",
        return_value=client,
    ):
        form = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], {"refresh_token": "replacement"}
        )
    assert result["errors"] == {"base": "wrong_account"}
    assert entry.data["refresh_token"] == "test-refresh"


async def test_options_reload_with_new_interval(hass, entry):
    with patch.object(hass.config_entries, "async_reload", new=AsyncMock()):
        form = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            form["flow_id"], {"scan_interval": 60, "fan_duration": 30}
        )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options == {"scan_interval": 60, "fan_duration": 30}


@pytest.mark.parametrize(
    "options",
    [
        {"scan_interval": 1, "fan_duration": 15},
        {"scan_interval": 901, "fan_duration": 15},
        {"scan_interval": 30, "fan_duration": 0},
        {"scan_interval": 30, "fan_duration": 721},
    ],
)
async def test_options_server_rejects_out_of_range_values(hass, entry, options):
    form = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(form["flow_id"], options)
    assert entry.options == {}
