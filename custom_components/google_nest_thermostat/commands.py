"""Validate thermostat commands before making any changes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .const import MAX_TEMP_C, MIN_RANGE_C, MIN_TEMP_C
from .models import NestThermostat, number


class CommandValidationError(ValueError):
    """A user-correctable command error, identified by a translation key."""


@dataclass(frozen=True, slots=True)
class Command:
    name: str
    params: dict[str, Any]


def set_mode(device: NestThermostat, mode: str) -> list[Command]:
    if mode not in device.available_modes:
        raise CommandValidationError("unsupported_mode")
    commands = []
    if device.is_eco:
        commands.append(Command("ThermostatEco.SetMode", {"mode": "OFF"}))
    if device.mode != mode:
        commands.append(Command("ThermostatMode.SetMode", {"mode": mode}))
    return commands


def _temperature(value: object) -> float:
    result = number(value)
    if result is None or not MIN_TEMP_C <= result <= MAX_TEMP_C:
        raise CommandValidationError("invalid_temperature")
    return result


def set_temperature(
    device: NestThermostat,
    *,
    temperature: object = None,
    low: object = None,
    high: object = None,
    mode: str | None = None,
) -> list[Command]:
    """Never turn an OFF thermostat on unless the caller explicitly asks."""
    effective_mode = mode if mode is not None else device.mode
    if effective_mode not in ("HEAT", "COOL", "HEATCOOL"):
        raise CommandValidationError("thermostat_off")
    if effective_mode not in device.available_modes:
        raise CommandValidationError("unsupported_mode")
    if device.is_eco:
        raise CommandValidationError("eco_active")
    # Validate the entire operation before sending even the mode change.
    if effective_mode == "HEATCOOL":
        if temperature is not None:
            raise CommandValidationError("range_required")
        if low is None and high is None:
            raise CommandValidationError("range_required")
        heat = _temperature(low if low is not None else device.heat_setpoint)
        cool = _temperature(high if high is not None else device.cool_setpoint)
        if cool - heat < MIN_RANGE_C:
            raise CommandValidationError("invalid_range")
        command = Command(
            "ThermostatTemperatureSetpoint.SetRange",
            {
                "heatCelsius": heat,
                "coolCelsius": cool,
            },
        )
    else:
        if low is not None or high is not None:
            raise CommandValidationError("single_temperature_required")
        value = _temperature(temperature)
        command = (
            Command("ThermostatTemperatureSetpoint.SetHeat", {"heatCelsius": value})
            if effective_mode == "HEAT"
            else Command(
                "ThermostatTemperatureSetpoint.SetCool", {"coolCelsius": value}
            )
        )
    return [*set_mode(device, effective_mode), command]


def set_eco(device: NestThermostat, eco: bool) -> list[Command]:
    if device.eco_mode is None:
        raise CommandValidationError("unsupported_preset")
    if eco and device.mode == "OFF":
        raise CommandValidationError("thermostat_off")
    target = "MANUAL_ECO" if eco else "OFF"
    return (
        []
        if device.eco_mode == target
        else [Command("ThermostatEco.SetMode", {"mode": target})]
    )


def set_fan(device: NestThermostat, mode: str, duration_minutes: int) -> list[Command]:
    if device.fan_timer_mode is None or mode not in ("auto", "on"):
        raise CommandValidationError("unsupported_fan")
    params: dict[str, Any] = {"timerMode": "ON" if mode == "on" else "OFF"}
    if mode == "on":
        params["duration"] = f"{duration_minutes * 60}s"
    return [Command("Fan.SetTimer", params)]
