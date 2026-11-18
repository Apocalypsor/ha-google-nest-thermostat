"""Defensive parsing of the documented Google SDM thermostat traits."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .const import TRAIT_PREFIX


def number(value: object) -> float | None:
    """Reject booleans, strings and non-finite readings."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def timestamp(value: object) -> datetime | None:
    """Parse a timezone-aware RFC 3339 time."""
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result.astimezone(UTC) if result.tzinfo is not None else None


@dataclass(frozen=True, slots=True, repr=False)
class NestTokens:
    """OAuth credentials; never include this object in logs or diagnostics."""

    access_token: str
    refresh_token: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class NestThermostat:
    """A complete, immutable REST snapshot; no event merging is required."""

    resource_name: str
    display_name: str
    room_name: str | None
    temperature: float | None
    humidity: float | None
    mode: str | None
    available_modes: tuple[str, ...]
    hvac_status: str | None
    heat_setpoint: float | None
    cool_setpoint: float | None
    eco_mode: str | None
    eco_heat: float | None
    eco_cool: float | None
    fan_timer_mode: str | None
    fan_timeout: datetime | None
    connectivity: str | None
    trait_names: frozenset[str]

    @property
    def is_online(self) -> bool:
        return self.connectivity != "OFFLINE"

    @property
    def is_eco(self) -> bool:
        return self.eco_mode == "MANUAL_ECO"

    @classmethod
    def from_api(cls, raw: object, project_id: str) -> NestThermostat | None:
        """Accept thermostats by their traits, and constrain command resource paths."""
        if not isinstance(raw, dict):
            return None
        name = raw.get("name")
        if not isinstance(name, str) or not re.fullmatch(
            rf"enterprises/{re.escape(project_id)}/devices/[A-Za-z0-9_-]+", name
        ):
            return None
        traits = raw.get("traits")
        if not isinstance(traits, dict):
            return None
        if TRAIT_PREFIX + "ThermostatMode" not in traits:
            return None

        def trait(key: str) -> dict[str, Any]:
            value = traits.get(TRAIT_PREFIX + key)
            return value if isinstance(value, dict) else {}

        def enum_value(key: str, field: str, allowed: tuple[str, ...]) -> str | None:
            value = trait(key).get(field)
            return value if isinstance(value, str) and value in allowed else None

        relations = raw.get("parentRelations")
        room = (
            next(
                (
                    item["displayName"].strip()
                    for item in relations
                    if isinstance(item, dict)
                    and isinstance(item.get("displayName"), str)
                    and item["displayName"].strip()
                ),
                None,
            )
            if isinstance(relations, list)
            else None
        )
        custom_name = trait("Info").get("customName")
        display = (
            custom_name.strip()
            if isinstance(custom_name, str) and custom_name.strip()
            else room or "Nest Thermostat"
        )
        available = trait("ThermostatMode").get("availableModes", [])
        modes = tuple(
            mode
            for mode in ("HEAT", "COOL", "HEATCOOL", "OFF")
            if isinstance(available, list) and mode in available
        )
        humidity = number(trait("Humidity").get("ambientHumidityPercent"))
        if humidity is not None and not 0 <= humidity <= 100:
            humidity = None
        return cls(
            resource_name=name,
            display_name=display,
            room_name=room,
            temperature=number(trait("Temperature").get("ambientTemperatureCelsius")),
            humidity=humidity,
            mode=enum_value(
                "ThermostatMode", "mode", ("HEAT", "COOL", "HEATCOOL", "OFF")
            ),
            available_modes=modes,
            hvac_status=enum_value(
                "ThermostatHvac", "status", ("HEATING", "COOLING", "OFF")
            ),
            heat_setpoint=number(
                trait("ThermostatTemperatureSetpoint").get("heatCelsius")
            ),
            cool_setpoint=number(
                trait("ThermostatTemperatureSetpoint").get("coolCelsius")
            ),
            eco_mode=enum_value("ThermostatEco", "mode", ("MANUAL_ECO", "OFF")),
            eco_heat=number(trait("ThermostatEco").get("heatCelsius")),
            eco_cool=number(trait("ThermostatEco").get("coolCelsius")),
            fan_timer_mode=enum_value("Fan", "timerMode", ("ON", "OFF")),
            fan_timeout=timestamp(trait("Fan").get("timerTimeout")),
            connectivity=enum_value("Connectivity", "status", ("ONLINE", "OFFLINE")),
            trait_names=frozenset(k for k in traits if isinstance(k, str)),
        )


@dataclass(frozen=True, slots=True)
class NestData:
    """Thermostats belonging to one Device Access project and linked user."""

    thermostats: dict[str, NestThermostat]
    account_id: str | None = None
