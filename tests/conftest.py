"""Sanitized thermostat data and a fake SDM server for real HA tests."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.google_nest_thermostat.const import DOMAIN
from custom_components.google_nest_thermostat.models import (
    NestData,
    NestThermostat,
    NestTokens,
)

PROJECT = "11111111-1111-4111-8111-111111111111"
RESOURCE = f"enterprises/{PROJECT}/devices/test-thermostat"
ACCOUNT = "test-account-digest"
TRAIT = "sdm.devices.traits."


def raw_device(mode="HEAT", temperature=21.5, resource=RESOURCE):
    return {
        "name": resource,
        "type": "sdm.devices.types.THERMOSTAT",
        "parentRelations": [{"parent": "test-room", "displayName": "Living Room"}],
        "traits": {
            TRAIT + "Info": {"customName": ""},
            TRAIT + "Temperature": {"ambientTemperatureCelsius": temperature},
            TRAIT + "Humidity": {"ambientHumidityPercent": 45},
            TRAIT + "Connectivity": {"status": "ONLINE"},
            TRAIT + "ThermostatMode": {
                "mode": mode,
                "availableModes": ["HEAT", "COOL", "HEATCOOL", "OFF"],
            },
            TRAIT + "ThermostatHvac": {"status": "OFF"},
            TRAIT + "ThermostatTemperatureSetpoint": {
                "heatCelsius": 20.0,
                "coolCelsius": 24.0,
            }
            if mode != "OFF"
            else {},
            TRAIT + "ThermostatEco": {
                "mode": "OFF",
                "heatCelsius": 10.0,
                "coolCelsius": 28.0,
            },
            TRAIT + "Fan": {"timerMode": "OFF"},
        },
    }


def device(**kwargs):
    result = NestThermostat.from_api(raw_device(**kwargs), PROJECT)
    assert result is not None
    return result


class FakeClient:
    def __init__(self, *_args, **_kwargs):
        self.data = NestData({RESOURCE: device()}, ACCOUNT)
        self.tokens = NestTokens("test-access", "test-refresh", 9_999_999_999)
        self.list_calls = 0
        self.get_calls = 0
        self.commands = []
        self.error = None
        self.on_command = None

    async def async_get_devices(self):
        self.list_calls += 1
        if self.error:
            raise self.error
        return self.data

    async def async_get_device(self, resource):
        self.get_calls += 1
        if self.error:
            raise self.error
        return self.data.thermostats[resource]

    async def async_execute_command(self, resource, command, params):
        self.commands.append((resource, command, deepcopy(params)))
        if self.on_command:
            self.on_command(resource, command, params)
            return
        current = self.data.thermostats[resource]
        fields = {}
        if command == "ThermostatMode.SetMode":
            fields["mode"] = params["mode"]
        elif command == "ThermostatEco.SetMode":
            fields["eco_mode"] = params["mode"]
        elif command.endswith(("SetHeat", "SetCool", "SetRange")):
            if "heatCelsius" in params:
                fields["heat_setpoint"] = params["heatCelsius"]
            if "coolCelsius" in params:
                fields["cool_setpoint"] = params["coolCelsius"]
        elif command == "Fan.SetTimer":
            fields["fan_timer_mode"] = params["timerMode"]
        self.data = NestData(
            {**self.data.thermostats, resource: replace(current, **fields)}, ACCOUNT
        )

    async def async_execute_commands(self, resource, commands):
        for command, params in commands:
            await self.async_execute_command(resource, command, params)


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture
def entry(hass):
    result = MockConfigEntry(
        domain=DOMAIN,
        title="Google Nest Thermostat",
        unique_id=f"{PROJECT}:{ACCOUNT}",
        data={
            "project_id": PROJECT,
            "client_id": "test.apps.googleusercontent.com",
            "client_secret": "test-client-secret",
            "refresh_token": "test-refresh",
            "access_token": "test-access",
            "expires_at": 9_999_999_999,
            "account_id": ACCOUNT,
        },
    )
    result.add_to_hass(hass)
    return result


@pytest.fixture
async def loaded_entry(hass, entry, client):
    with patch(
        "custom_components.google_nest_thermostat.NestApiClient", return_value=client
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
