"""Test OAuth, HTTP errors and local quotas without network access."""

import asyncio
from unittest.mock import Mock, patch

import aiohttp
import pytest

from custom_components.google_nest_thermostat.api import (
    NestApiClient,
    NestAuthenticationError,
    NestCommandError,
    NestConnectionError,
    NestPermissionError,
    NestRateLimitError,
    RequestBudget,
)
from custom_components.google_nest_thermostat.const import API_BASE_URL, TOKEN_URL

from .conftest import PROJECT, RESOURCE, raw_device


class Response:
    def __init__(self, status=200, body=None, headers=None):
        self.status = status
        self.body = {} if body is None else body
        self.headers = headers or {"User-Id": "fixture-user"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def json(self, **_kwargs):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


def make_client(session, **kwargs):
    return NestApiClient(
        session,
        project_id=PROJECT,
        client_id="test-client",
        client_secret="test-secret",
        refresh_token="initial-refresh",
        **kwargs,
    )


async def test_refresh_rotation_saved_before_first_authenticated_read():
    saved = []
    session = Mock()
    session.post.return_value = Response(
        body={
            "access_token": "new-access",
            "refresh_token": "rotated-refresh",
            "expires_in": 3600,
        }
    )

    def request(*_args, **kwargs):
        assert saved[0].refresh_token == "rotated-refresh"
        assert kwargs["headers"] == {"Authorization": "Bearer new-access"}
        return Response(body={"devices": [raw_device()]})

    session.request.side_effect = request

    async def save(tokens):
        saved.append(tokens)

    client = make_client(session, token_update_callback=save)
    data = await client.async_get_devices()
    assert len(data.thermostats) == 1 and data.account_id is not None
    assert client.tokens == saved[0]
    assert session.post.call_args.args == (TOKEN_URL,)
    assert session.request.call_args.args == (
        "GET",
        f"{API_BASE_URL}/enterprises/{PROJECT}/devices",
    )


async def test_concurrent_refresh_uses_one_request_and_keeps_unrotated_refresh():
    session = Mock()
    session.post.return_value = Response(
        body={"access_token": "new", "expires_in": 3600}
    )
    client = make_client(session)
    assert (
        await asyncio.gather(*(client._async_access_token() for _ in range(5)))
        == ["new"] * 5
    )
    assert session.post.call_count == 1
    assert client.tokens.refresh_token == "initial-refresh"


async def test_valid_epoch_token_reused_without_timezone_conversion():
    session = Mock()
    client = make_client(session, access_token="cached", expires_at=9_999_999_999)
    assert await client._async_access_token() == "cached"
    session.post.assert_not_called()


@pytest.mark.parametrize("second_status", [200, 401])
async def test_one_401_refresh_and_one_retry_only(second_status):
    session = Mock()
    session.post.return_value = Response(
        body={"access_token": "replacement", "expires_in": 3600}
    )
    session.request.side_effect = [
        Response(401),
        Response(second_status, {"devices": []}),
    ]
    client = make_client(session, access_token="rejected", expires_at=9_999_999_999)
    if second_status == 200:
        await client.async_get_devices()
    else:
        with pytest.raises(NestAuthenticationError):
            await client.async_get_devices()
    assert session.request.call_count == 2
    assert session.post.call_count == 1


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (400, NestAuthenticationError),
        (401, NestAuthenticationError),
        (403, NestAuthenticationError),
        (429, NestRateLimitError),
        (503, NestConnectionError),
    ],
)
async def test_refresh_failures_are_safe(status, error):
    session = Mock()
    session.post.return_value = Response(
        status, {"error_description": "secret-do-not-log"}
    )
    with pytest.raises(error) as exc:
        await make_client(session)._async_access_token()
    assert "secret-do-not-log" not in str(exc.value)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"access_token": "x", "expires_in": float("nan")},
        {"access_token": "x", "expires_in": True},
        {"access_token": "x", "expires_in": 0},
        {"expires_in": 3600},
    ],
)
async def test_bad_token_response(payload):
    session = Mock()
    session.post.return_value = Response(body=payload)
    with pytest.raises(NestConnectionError):
        await make_client(session)._async_access_token()


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (403, NestPermissionError),
        (404, NestPermissionError),
        (400, NestCommandError),
        (412, NestCommandError),
        (429, NestRateLimitError),
        (503, NestConnectionError),
    ],
)
async def test_http_statuses(status, error):
    session = Mock()
    session.request.return_value = Response(status, headers={"Retry-After": "120"})
    with pytest.raises(error) as exc:
        await make_client(
            session, access_token="valid", expires_at=9_999_999_999
        ).async_get_devices()
    if status == 429:
        assert exc.value.retry_after == 120
    session.post.assert_not_called()
    assert session.request.call_count == 1


async def test_transport_error_does_not_leak_server_details():
    session = Mock()
    session.request.side_effect = aiohttp.ClientConnectionError("secret-server-body")
    with pytest.raises(NestConnectionError, match="Could not read"):
        await make_client(
            session, access_token="valid", expires_at=9_999_999_999
        ).async_get_devices()


async def test_commands_use_celsius_and_only_sdm_endpoint():
    session = Mock()
    session.request.return_value = Response()
    client = make_client(session, access_token="valid", expires_at=9_999_999_999)
    await client.async_execute_command(
        RESOURCE, "ThermostatTemperatureSetpoint.SetHeat", {"heatCelsius": 20.5}
    )
    call = session.request.call_args
    assert call.args == ("POST", f"{API_BASE_URL}/{RESOURCE}:executeCommand")
    assert call.kwargs["json"] == {
        "command": "sdm.devices.commands.ThermostatTemperatureSetpoint.SetHeat",
        "params": {"heatCelsius": 20.5},
    }


async def test_wrong_project_resource_is_never_requested():
    session = Mock()
    with pytest.raises(NestCommandError):
        await make_client(session).async_get_device("enterprises/other/devices/test")
    session.request.assert_not_called()
    session.post.assert_not_called()


def test_rate_limit_guards_all_budgets_atomically():
    budget = RequestBudget()
    with patch(
        "custom_components.google_nest_thermostat.api.time.monotonic", return_value=100
    ):
        for _ in range(5):
            budget.check([("minute", 5, 60), ("hour", 100, 3600)])
        with pytest.raises(NestRateLimitError):
            budget.check([("minute", 5, 60), ("hour", 100, 3600)])
        assert len(budget._calls["hour"]) == 5
    with patch(
        "custom_components.google_nest_thermostat.api.time.monotonic", return_value=161
    ):
        budget.check([("minute", 5, 60), ("hour", 100, 3600)])
        assert len(budget._calls["minute"]) == 1


async def test_multi_command_action_reserves_budget_before_any_side_effect():
    session = Mock()
    session.request.return_value = Response()
    client = make_client(session, access_token="valid", expires_at=9_999_999_999)
    for _ in range(4):
        await client.async_execute_command(
            RESOURCE, "Fan.SetTimer", {"timerMode": "OFF"}
        )
    count = session.request.call_count
    with pytest.raises(NestRateLimitError):
        await client.async_execute_commands(
            RESOURCE,
            [
                ("ThermostatEco.SetMode", {"mode": "OFF"}),
                ("ThermostatMode.SetMode", {"mode": "OFF"}),
            ],
        )
    assert session.request.call_count == count
    assert len(client._budget._calls[f"device_minute:{RESOURCE}"]) == 4
