"""Async SDM REST transport and OAuth refresh, with no subscription dependency."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import aiohttp

from .const import (
    API_BASE_URL,
    COMMAND_PREFIX,
    REFRESH_MARGIN,
    REQUEST_TIMEOUT,
    TOKEN_URL,
)
from .models import NestData, NestThermostat, NestTokens


class NestError(Exception):
    """A sanitized SDM error without credentials or response bodies."""


class NestAuthenticationError(NestError):
    """The OAuth grant is invalid or expired."""


class NestPermissionError(NestError):
    """The account or project does not have the required SDM access."""


class NestConnectionError(NestError):
    """Google could not be reached or returned an invalid response."""


class NestCommandError(NestError):
    """Google rejected a command in the device's current state."""


class NestRateLimitError(NestError):
    """A local quota guard or Google requires a pause."""

    def __init__(self, retry_after: float = 60) -> None:
        self.retry_after = max(1, min(retry_after, 3600))
        super().__init__("Google SDM rate limit reached")


def normalize_project_id(value: str) -> str:
    """The SDM project ID is a UUID, not a Google Cloud project name."""
    try:
        return str(UUID(value.strip()))
    except (ValueError, AttributeError) as err:
        raise ValueError("Invalid Device Access project ID") from err


class RequestBudget:
    """Guard documented per-user/project and thermostat command quotas locally."""

    def __init__(self) -> None:
        self._calls: dict[str, deque[float]] = defaultdict(deque)

    def check(self, limits: list[tuple[str, int, int]]) -> None:
        """Reserve all budgets atomically on the event loop; never queue commands."""
        now = time.monotonic()
        waits = []
        reservations: dict[str, tuple[int, int, int]] = {}
        for key, count, seconds in limits:
            weight = reservations.get(key, (count, seconds, 0))[2] + 1
            reservations[key] = (count, seconds, weight)
        for key, (count, seconds, weight) in reservations.items():
            calls = self._calls[key]
            while calls and calls[0] <= now - seconds:
                calls.popleft()
            if weight > count:
                raise NestRateLimitError(seconds)
            if len(calls) + weight > count:
                release_index = len(calls) + weight - count - 1
                waits.append(calls[release_index] + seconds - now)
        if waits:
            raise NestRateLimitError(max(waits) + 0.1)
        for key, _, _ in limits:
            self._calls[key].append(now)


class NestApiClient:
    """Use official OAuth and SDM HTTP endpoints only."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        project_id: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        access_token: str | None = None,
        expires_at: float = 0,
        token_update_callback: Callable[[NestTokens], Awaitable[None]] | None = None,
    ) -> None:
        self.project_id = normalize_project_id(project_id)
        self._session = session
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._tokens = NestTokens(access_token or "", refresh_token.strip(), expires_at)
        self._token_update_callback = token_update_callback
        self._refresh_lock = asyncio.Lock()
        self._budget = RequestBudget()

    @property
    def tokens(self) -> NestTokens:
        return self._tokens

    async def _async_access_token(self, rejected_token: str | None = None) -> str:
        async with self._refresh_lock:
            current = self._tokens
            if current.access_token and (
                (rejected_token is not None and current.access_token != rejected_token)
                or (
                    rejected_token is None
                    and current.expires_at > time.time() + REFRESH_MARGIN
                )
            ):
                return current.access_token
            try:
                async with self._session.post(
                    TOKEN_URL,
                    data={
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "refresh_token": current.refresh_token,
                        "grant_type": "refresh_token",
                    },
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                ) as response:
                    if response.status in (400, 401, 403):
                        raise NestAuthenticationError(
                            "Google authorization must be renewed"
                        )
                    if response.status == 429:
                        raise NestRateLimitError()
                    if response.status >= 400:
                        raise NestConnectionError("Google token service is unavailable")
                    payload = await response.json(content_type=None)
            except aiohttp.ClientError, TimeoutError, ValueError:
                raise NestConnectionError(
                    "Could not refresh Google authorization"
                ) from None
            if not isinstance(payload, dict):
                raise NestConnectionError("Invalid Google token response")
            access = payload.get("access_token")
            refresh = payload.get("refresh_token", current.refresh_token)
            lifetime = payload.get("expires_in")
            if (
                not isinstance(access, str)
                or not access
                or not isinstance(refresh, str)
                or not refresh
                or isinstance(lifetime, bool)
                or not isinstance(lifetime, (int, float))
                or not math.isfinite(lifetime)
                or lifetime <= 0
            ):
                raise NestConnectionError("Invalid Google token response")
            tokens = NestTokens(access, refresh, time.time() + lifetime)
            # Persist rotation before another request can use the new credentials.
            if self._token_update_callback is not None:
                await self._token_update_callback(tokens)
            self._tokens = tokens
            return tokens.access_token

    async def _async_request(
        self, method: str, path: str, *, payload: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], str | None]:
        token = await self._async_access_token()
        for attempt in range(2):
            try:
                async with self._session.request(
                    method,
                    f"{API_BASE_URL}/{path}",
                    headers={"Authorization": f"Bearer {token}"},
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                ) as response:
                    status = response.status
                    if status == 401:
                        if attempt:
                            raise NestAuthenticationError(
                                "Google authorization was rejected"
                            )
                    elif status == 403:
                        raise NestPermissionError("Google SDM access is not permitted")
                    elif status == 429:
                        try:
                            delay = float(response.headers.get("Retry-After", "60"))
                            if not math.isfinite(delay):
                                delay = 60
                        except ValueError:
                            delay = 60
                        raise NestRateLimitError(delay)
                    elif status == 404:
                        raise NestPermissionError(
                            "Google SDM project or device was not found"
                        )
                    elif status in (400, 409, 412):
                        raise NestCommandError(
                            "Command is not allowed in the current device state"
                        )
                    elif status >= 400:
                        raise NestConnectionError("Google SDM service is unavailable")
                    else:
                        body = await response.json(content_type=None)
                        if not isinstance(body, dict):
                            raise NestConnectionError("Invalid Google SDM response")
                        user_id = response.headers.get("User-Id")
                        account_id = (
                            hashlib.sha256(
                                f"{self.project_id}:{user_id}".encode()
                            ).hexdigest()
                            if user_id
                            else None
                        )
                        return body, account_id
            except aiohttp.ClientError, TimeoutError, ValueError, json.JSONDecodeError:
                raise NestConnectionError(
                    "Could not read Google SDM response"
                ) from None
            # Exactly one forced refresh/retry for a rejected bearer token.
            token = await self._async_access_token(rejected_token=token)
        raise NestAuthenticationError("Google authorization was rejected")

    async def async_get_devices(self) -> NestData:
        self._budget.check([("list", 5, 60)])
        body, account_id = await self._async_request(
            "GET", f"enterprises/{self.project_id}/devices"
        )
        devices = body.get("devices", [])
        if not isinstance(devices, list):
            raise NestConnectionError("Invalid Google device list")
        thermostats = {
            parsed.resource_name: parsed
            for raw in devices
            if (parsed := NestThermostat.from_api(raw, self.project_id)) is not None
        }
        return NestData(thermostats, account_id)

    def _check_resource(self, resource_name: str) -> None:
        prefix = f"enterprises/{self.project_id}/devices/"
        if (
            not resource_name.startswith(prefix)
            or not resource_name.removeprefix(prefix)
            .replace("-", "")
            .replace("_", "")
            .isalnum()
        ):
            raise NestCommandError("Invalid thermostat resource")

    async def async_get_device(self, resource_name: str) -> NestThermostat:
        self._check_resource(resource_name)
        self._budget.check([("get", 10, 60)])
        body, _ = await self._async_request("GET", resource_name)
        device = NestThermostat.from_api(body, self.project_id)
        if device is None or device.resource_name != resource_name:
            raise NestConnectionError("Invalid thermostat response")
        return device

    async def async_execute_command(
        self, resource_name: str, command: str, params: dict[str, Any]
    ) -> None:
        await self.async_execute_commands(resource_name, [(command, params)])

    async def async_execute_commands(
        self, resource_name: str, commands: list[tuple[str, dict[str, Any]]]
    ) -> None:
        """Reserve a whole action's quota before sending its first command."""
        self._check_resource(resource_name)
        self._budget.check(
            [
                limit
                for command, _ in commands
                for limit in [
                    ("execute", 10, 60),
                    (f"command:{resource_name}:{command}", 5, 60),
                    (f"device_minute:{resource_name}", 5, 60),
                    (f"device_hour:{resource_name}", 100, 3600),
                ]
            ]
        )
        for command, params in commands:
            await self._async_request(
                "POST",
                f"{resource_name}:executeCommand",
                payload={"command": COMMAND_PREFIX + command, "params": params},
            )
