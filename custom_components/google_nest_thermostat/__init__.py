"""Google Nest thermostat state and control through polling only."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import NestApiClient
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_EXPIRES_AT,
    CONF_PROJECT_ID,
    CONF_REFRESH_TOKEN,
    PLATFORMS,
)
from .coordinator import NestCoordinator
from .models import NestTokens


@dataclass(slots=True)
class NestRuntimeData:
    client: NestApiClient
    coordinator: NestCoordinator
    options: dict[str, object]


type NestConfigEntry = ConfigEntry[NestRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: NestConfigEntry) -> bool:
    """Set up an independent SDM client, never the built-in Nest subscriber."""

    async def save_tokens(tokens: NestTokens) -> None:
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_REFRESH_TOKEN: tokens.refresh_token,
                CONF_ACCESS_TOKEN: tokens.access_token,
                CONF_EXPIRES_AT: tokens.expires_at,
            },
        )

    client = NestApiClient(
        async_get_clientsession(hass),
        project_id=entry.data[CONF_PROJECT_ID],
        client_id=entry.data[CONF_CLIENT_ID],
        client_secret=entry.data[CONF_CLIENT_SECRET],
        refresh_token=entry.data[CONF_REFRESH_TOKEN],
        access_token=entry.data.get(CONF_ACCESS_TOKEN),
        expires_at=entry.data.get(CONF_EXPIRES_AT, 0),
        token_update_callback=save_tokens,
    )
    coordinator = NestCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = NestRuntimeData(client, coordinator, dict(entry.options))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def _options_updated(hass: HomeAssistant, entry: NestConfigEntry) -> None:
    # Token rotation updates entry.data too; it must not restart entity polling.
    if entry.options != entry.runtime_data.options:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: NestConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
