"""Exercise actual HA entity state, polling, services and availability."""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.google_nest_thermostat.api import (
    NestAuthenticationError,
    NestConnectionError,
    NestRateLimitError,
)
from custom_components.google_nest_thermostat.commands import set_mode
from custom_components.google_nest_thermostat.const import DOMAIN
from custom_components.google_nest_thermostat.coordinator import NestCoordinator
from custom_components.google_nest_thermostat.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.google_nest_thermostat.models import NestData

from .conftest import ACCOUNT, RESOURCE, device


def entity(hass, platform="climate", suffix=""):
    result = er.async_get(hass).async_get_entity_id(platform, DOMAIN, RESOURCE + suffix)
    assert result is not None
    return result


async def test_setup_creates_native_climate_and_sensors(hass, loaded_entry):
    state = hass.states.get(entity(hass))
    assert state.state == "heat"
    assert state.attributes["current_temperature"] == 21.5
    assert state.attributes["temperature"] == 20
    assert state.attributes["hvac_action"] == "idle"
    assert "heat_cool" in state.attributes["hvac_modes"]
    assert "auto" not in state.attributes["hvac_modes"]
    assert hass.states.get(entity(hass, "sensor", "-temperature")).state == "21.5"
    assert hass.states.get(entity(hass, "sensor", "-humidity")).state == "45.0"
    assert hass.states.get(entity(hass, "sensor", "-fan_timeout")).state == "unknown"


async def test_rest_refresh_updates_every_entity_without_events(
    hass, loaded_entry, client
):
    client.data = NestData(
        {RESOURCE: replace(device(temperature=23.125), humidity=51)}, ACCOUNT
    )
    await loaded_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity(hass)).attributes["current_temperature"] == 23.1
    assert (
        float(hass.states.get(entity(hass, "sensor", "-temperature")).state) == 23.125
    )
    assert float(hass.states.get(entity(hass, "sensor", "-humidity")).state) == 51


async def test_scheduled_poll_is_shared_across_entities(
    hass, loaded_entry, client, freezer
):
    await hass.async_start()
    baseline = client.list_calls
    client.data = NestData({RESOURCE: device(temperature=24)}, ACCOUNT)
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert client.list_calls == baseline + 1
    assert hass.states.get(entity(hass)).attributes["current_temperature"] == 24


async def test_command_is_read_back_and_all_entities_update(hass, loaded_entry, client):
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": entity(hass), "temperature": 22},
        blocking=True,
    )
    assert client.commands[-1][1:] == (
        "ThermostatTemperatureSetpoint.SetHeat",
        {"heatCelsius": 22},
    )
    assert client.get_calls == 2
    assert hass.states.get(entity(hass)).attributes["temperature"] == 22


async def test_readback_never_claims_requested_value_before_google_confirms(
    hass, loaded_entry, client
):
    client.on_command = lambda *_args: None
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": entity(hass), "temperature": 22},
        blocking=True,
    )
    assert hass.states.get(entity(hass)).attributes["temperature"] == 20


async def test_fresh_read_prevents_heating_an_offline_or_now_off_thermostat(
    hass, loaded_entry, client
):
    client.data = NestData({RESOURCE: device(mode="OFF")}, ACCOUNT)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": entity(hass), "temperature": 22},
            blocking=True,
        )
    assert not client.commands


async def test_mode_fan_and_eco_controls(hass, loaded_entry, client):
    name = entity(hass)
    for service, args in [
        ("set_hvac_mode", {"hvac_mode": "heat_cool"}),
        ("set_temperature", {"target_temp_low": 19, "target_temp_high": 24}),
        ("set_preset_mode", {"preset_mode": "eco"}),
        ("set_preset_mode", {"preset_mode": "none"}),
        ("set_fan_mode", {"fan_mode": "on"}),
    ]:
        await hass.services.async_call(
            "climate", service, {"entity_id": name, **args}, blocking=True
        )
    assert client.commands[-1][1:] == (
        "Fan.SetTimer",
        {"timerMode": "ON", "duration": "900s"},
    )
    assert hass.states.get(name).state == "heat_cool"


async def test_temperature_services_convert_fahrenheit_to_api_celsius(
    hass, loaded_entry, client
):
    hass.config.units = US_CUSTOMARY_SYSTEM
    # Tell entities HA's display units changed before calling the service.
    await loaded_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": entity(hass), "temperature": 68},
        blocking=True,
    )
    assert client.commands[-1][2] == {"heatCelsius": 20}
    assert hass.states.get(entity(hass)).attributes["temperature"] == 68


async def test_removed_and_offline_devices_unavailable_then_recover(
    hass, loaded_entry, client
):
    coord = loaded_entry.runtime_data.coordinator
    client.data = NestData({}, ACCOUNT)
    await coord.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity(hass)).state == "unavailable"
    client.data = NestData(
        {RESOURCE: replace(device(), connectivity="OFFLINE")}, ACCOUNT
    )
    await coord.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity(hass)).state == "unavailable"
    client.data = NestData({RESOURCE: device()}, ACCOUNT)
    await coord.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity(hass)).state == "heat"


async def test_later_discovery_adds_entities_once(hass, loaded_entry, client):
    second = device(resource=RESOURCE + "-second")
    client.data = NestData(
        {**client.data.thermostats, second.resource_name: second}, ACCOUNT
    )
    await loaded_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    climates = hass.states.async_all("climate")
    assert len(climates) == 2
    await loaded_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert len(hass.states.async_all("climate")) == 2


async def test_rate_limit_and_transport_backoff_recover(hass, loaded_entry, client):
    coord = loaded_entry.runtime_data.coordinator
    client.error = NestRateLimitError(120)
    await coord.async_refresh()
    await hass.async_block_till_done()
    assert (
        not coord.last_update_success and coord.update_interval.total_seconds() >= 120
    )
    assert hass.states.get(entity(hass)).state == "unavailable"
    client.error = NestConnectionError("Google unavailable")
    await coord.async_refresh()
    client.error = None
    await coord.async_refresh()
    await hass.async_block_till_done()
    assert coord.last_update_success and coord.update_interval.total_seconds() == 30
    assert hass.states.get(entity(hass)).state == "heat"


async def test_auth_error_requests_reauth(hass, entry, client):
    client.error = NestAuthenticationError()
    coord = NestCoordinator(hass, entry, client)
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()


async def test_changed_account_rejected(hass, entry, client):
    client.data = NestData(client.data.thermostats, "different-user")
    with pytest.raises(ConfigEntryAuthFailed):
        await NestCoordinator(hass, entry, client)._async_update_data()


async def test_control_auth_failure_starts_reauth(hass, loaded_entry, client):
    client.error = NestAuthenticationError()
    with (
        patch.object(type(loaded_entry), "async_start_reauth") as reauth,
        pytest.raises(HomeAssistantError),
    ):
        await loaded_entry.runtime_data.coordinator.async_control(
            RESOURCE, lambda d: set_mode(d, "OFF")
        )
    reauth.assert_called_once()


async def test_diagnostics_exclude_all_identifiers_and_credentials(hass, loaded_entry):
    import json

    data = await async_get_config_entry_diagnostics(hass, loaded_entry)
    text = json.dumps(data)
    for private in [*loaded_entry.data.values(), RESOURCE, "Living Room"]:
        if isinstance(private, str):
            assert private not in text
    assert data["transport"] == "sdm_rest_polling"


async def test_unload_stops_polling(hass, entry, client, freezer):
    with patch(
        "custom_components.google_nest_thermostat.NestApiClient", return_value=client
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        calls = client.list_calls
        freezer.tick(timedelta(seconds=90))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
        assert client.list_calls == calls


async def test_setup_persists_rotated_tokens(hass, entry):
    client = AsyncMock()
    client.async_get_devices.return_value = NestData({RESOURCE: device()}, ACCOUNT)
    captured = {}

    def factory(*_args, **kwargs):
        captured.update(kwargs)
        return client

    with patch(
        "custom_components.google_nest_thermostat.NestApiClient", side_effect=factory
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        from custom_components.google_nest_thermostat.models import NestTokens

        await captured["token_update_callback"](
            NestTokens("rotated-access", "rotated-refresh", 12345)
        )
        assert entry.data["refresh_token"] == "rotated-refresh"
        assert entry.data["access_token"] == "rotated-access"
        await hass.config_entries.async_unload(entry.entry_id)


async def test_token_refresh_does_not_reload_the_integration(hass, entry):
    from .conftest import FakeClient

    captured = {}

    def factory(*_args, **kwargs):
        captured.update(kwargs)
        return FakeClient()

    with patch(
        "custom_components.google_nest_thermostat.NestApiClient", side_effect=factory
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        from custom_components.google_nest_thermostat.models import NestTokens

        with patch.object(
            hass.config_entries, "async_reload", new=AsyncMock()
        ) as reload:
            await captured["token_update_callback"](
                NestTokens("rotated", "rotated-refresh", 12345)
            )
            await hass.async_block_till_done()
            reload.assert_not_awaited()
            hass.config_entries.async_update_entry(entry, options={"scan_interval": 60})
            await hass.async_block_till_done()
            reload.assert_awaited_once_with(entry.entry_id)
        await hass.config_entries.async_unload(entry.entry_id)


async def test_explicit_mode_and_target_work_in_one_ha_service(
    hass, loaded_entry, client
):
    client.data = NestData({RESOURCE: device(mode="OFF")}, ACCOUNT)
    await loaded_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {
            "entity_id": entity(hass),
            "hvac_mode": "heat",
            "temperature": 21,
        },
        blocking=True,
    )
    assert [row[1] for row in client.commands] == [
        "ThermostatMode.SetMode",
        "ThermostatTemperatureSetpoint.SetHeat",
    ]
    assert hass.states.get(entity(hass)).state == "heat"


async def test_control_does_not_postpone_other_devices_poll(
    hass, loaded_entry, client, freezer
):
    await hass.async_start()
    baseline = client.list_calls
    freezer.tick(timedelta(seconds=20))
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {
            "entity_id": entity(hass),
            "temperature": 22,
        },
        blocking=True,
    )
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert client.list_calls == baseline + 1


async def test_rejected_command_keeps_confirmed_device_available(
    hass, loaded_entry, client
):
    from custom_components.google_nest_thermostat.api import NestCommandError

    def reject(*_args):
        raise NestCommandError("Rejected by Google")

    client.on_command = reject
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "climate",
            "set_temperature",
            {
                "entity_id": entity(hass),
                "temperature": 22,
            },
            blocking=True,
        )
    assert hass.states.get(entity(hass)).state == "heat"


async def test_missing_identity_never_silently_replaces_the_linked_account(
    hass, entry, client
):
    client.data = NestData(client.data.thermostats, None)
    with pytest.raises(ConfigEntryAuthFailed):
        await NestCoordinator(hass, entry, client)._async_update_data()
