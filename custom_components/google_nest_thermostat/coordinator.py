"""Poll complete SDM snapshots and publish real reads to HA entities."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    NestApiClient,
    NestAuthenticationError,
    NestCommandError,
    NestError,
    NestRateLimitError,
)
from .commands import Command, CommandValidationError
from .const import (
    CONF_ACCOUNT_ID,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_BACKOFF,
)
from .models import NestData, NestThermostat

_LOGGER = logging.getLogger(__name__)


class NestCoordinator(DataUpdateCoordinator[NestData]):
    """Share a single poll across every entity and serialize commands with reads."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: NestApiClient
    ) -> None:
        self.entry = entry
        self.client = client
        self.poll_seconds = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        self._failures = 0
        self._operation_lock = asyncio.Lock()
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=self.poll_seconds),
            always_update=False,
        )

    def _backoff(self, minimum: float = 0) -> None:
        self._failures = min(self._failures + 1, 6)
        seconds = max(minimum, min(MAX_BACKOFF, self.poll_seconds * 2**self._failures))
        self.update_interval = timedelta(seconds=seconds)

    def _reset_backoff(self) -> None:
        self._failures = 0
        self.update_interval = timedelta(seconds=self.poll_seconds)

    async def _async_update_data(self) -> NestData:
        try:
            async with self._operation_lock:
                data = await self.client.async_get_devices()
            expected = self.entry.data.get(CONF_ACCOUNT_ID)
            if expected and expected != data.account_id:
                raise NestAuthenticationError("Google linked user changed")
        except NestAuthenticationError:
            raise ConfigEntryAuthFailed(
                "Google authorization must be renewed"
            ) from None
        except NestRateLimitError as err:
            self._backoff(err.retry_after)
            raise UpdateFailed("Google SDM rate limit reached; retry delayed") from None
        except NestError as err:
            self._backoff()
            raise UpdateFailed(str(err)) from None
        self._reset_backoff()
        return data

    @callback
    def _apply_confirmed_device(self, device: NestThermostat) -> None:
        """Notify entities without postponing the shared all-device poll.

        A successful single-device read must not mark stale peers healthy when
        the last full poll failed, or continually reset their polling timer.
        """
        assert self.data is not None
        self.data = NestData(
            {**self.data.thermostats, device.resource_name: device},
            self.data.account_id,
        )
        self.async_update_listeners()

    async def async_control(
        self,
        resource_name: str,
        prepare: Callable[[NestThermostat], list[Command]],
    ) -> None:
        """Validate against a live read, issue commands, then read actual state back."""
        try:
            async with self._operation_lock:
                if self.data is None or resource_name not in self.data.thermostats:
                    raise CommandValidationError("device_unavailable")
                device = await self.client.async_get_device(resource_name)
                if not device.is_online:
                    raise CommandValidationError("device_unavailable")
                commands = prepare(device)
                try:
                    await self.client.async_execute_commands(
                        resource_name,
                        [(command.name, command.params) for command in commands],
                    )
                except NestError:
                    # Reconcile partial success, but preserve the original error
                    # if the follow-up read also fails.
                    if commands:
                        try:
                            device = await self.client.async_get_device(resource_name)
                        except NestError:
                            pass
                        else:
                            self._apply_confirmed_device(device)
                    raise
                else:
                    if commands:
                        device = await self.client.async_get_device(resource_name)
                        self._apply_confirmed_device(device)
        except CommandValidationError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key=str(err)
            ) from None
        except NestAuthenticationError:
            self.entry.async_start_reauth(self.hass)
            self.async_set_update_error(
                ConfigEntryAuthFailed("Google authorization expired")
            )
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from None
        except NestRateLimitError as err:
            self._backoff(err.retry_after)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="rate_limited",
                translation_placeholders={"seconds": str(math.ceil(err.retry_after))},
            ) from None
        except NestCommandError:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="command_rejected"
            ) from None
        except NestError as err:
            self.async_set_update_error(UpdateFailed(str(err)))
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="command_failed"
            ) from None
