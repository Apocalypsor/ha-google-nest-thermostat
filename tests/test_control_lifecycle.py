"""Extra coverage for controls with effects across multiple SDM commands."""

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.google_nest_thermostat.api import NestCommandError

from .test_integration import entity


async def test_turn_on_restores_mode_after_turn_off(hass, loaded_entry, client):
    name = entity(hass)
    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {
            "entity_id": name,
            "hvac_mode": "cool",
        },
        blocking=True,
    )
    await hass.services.async_call(
        "climate", "turn_off", {"entity_id": name}, blocking=True
    )
    assert hass.states.get(name).state == "off"
    await hass.services.async_call(
        "climate", "turn_on", {"entity_id": name}, blocking=True
    )
    assert hass.states.get(name).state == "cool"
    assert client.commands[-1][2] == {"mode": "COOL"}


async def test_partial_success_is_read_back_without_retrying_action(
    hass, loaded_entry, client
):
    name = entity(hass)
    await hass.services.async_call(
        "climate",
        "set_preset_mode",
        {
            "entity_id": name,
            "preset_mode": "eco",
        },
        blocking=True,
    )
    original = client.async_execute_command

    async def fail_second(resource, command, params):
        if command == "ThermostatMode.SetMode":
            raise NestCommandError("Cannot change mode")
        await original(resource, command, params)

    client.async_execute_command = fail_second
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {
                "entity_id": name,
                "hvac_mode": "cool",
            },
            blocking=True,
        )
    # Eco was successfully exited, but mode remains the last confirmed HEAT.
    state = hass.states.get(name)
    assert state.state == "heat" and state.attributes["preset_mode"] == "none"
    assert not any(c[1] == "ThermostatMode.SetMode" for c in client.commands)
