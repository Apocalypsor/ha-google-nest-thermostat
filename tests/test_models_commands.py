"""Model parsing and validated SDM command semantics."""

from dataclasses import replace

import pytest

from custom_components.google_nest_thermostat.commands import (
    CommandValidationError,
    set_eco,
    set_fan,
    set_mode,
    set_temperature,
)
from custom_components.google_nest_thermostat.models import (
    NestThermostat,
    number,
    timestamp,
)

from .conftest import PROJECT, TRAIT, device, raw_device


def test_off_has_no_setpoint_and_never_uses_eco_as_normal_target():
    d = device(mode="OFF")
    assert d.temperature == 21.5 and d.heat_setpoint is None
    assert d.display_name == "Living Room" and d.is_online


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {},
        {
            "name": "enterprises/other/devices/foo",
            "traits": {TRAIT + "ThermostatMode": {}},
        },
    ],
)
def test_malformed_or_wrong_project_devices_ignored(bad):
    assert NestThermostat.from_api(bad, PROJECT) is None


def test_missing_optional_traits_and_invalid_humidity():
    raw = raw_device()
    raw["traits"].pop(TRAIT + "Fan")
    raw["traits"][TRAIT + "Humidity"]["ambientHumidityPercent"] = 120
    parsed = NestThermostat.from_api(raw, PROJECT)
    assert parsed.fan_timer_mode is None and parsed.humidity is None
    raw["parentRelations"] = None
    raw["traits"][TRAIT + "Info"]["customName"] = "Upstairs"
    assert NestThermostat.from_api(raw, PROJECT).display_name == "Upstairs"


@pytest.mark.parametrize("bad", [True, "21", float("nan"), float("inf"), None])
def test_non_numeric_values_rejected(bad):
    assert number(bad) is None


def test_timestamp_never_assumes_local_timezone():
    assert timestamp("2027-01-01T12:00:00Z").utcoffset().total_seconds() == 0
    assert timestamp("2027-01-01T12:00:00") is None
    assert timestamp("invalid") is None


def test_heat_cool_maps_to_range_command():
    result = set_temperature(device(mode="HEATCOOL"), low=19, high=24)
    assert len(result) == 1
    assert result[0].name.endswith("SetRange")
    assert result[0].params == {"heatCelsius": 19, "coolCelsius": 24}


def test_off_needs_explicit_mode_and_invalid_input_never_changes_mode():
    with pytest.raises(CommandValidationError, match="thermostat_off"):
        set_temperature(device(mode="OFF"), temperature=20)
    with pytest.raises(CommandValidationError, match="invalid_temperature"):
        set_temperature(device(mode="OFF"), mode="HEAT", temperature=50)
    result = set_temperature(device(mode="OFF"), mode="HEAT", temperature=20)
    assert [x.name for x in result] == [
        "ThermostatMode.SetMode",
        "ThermostatTemperatureSetpoint.SetHeat",
    ]


@pytest.mark.parametrize("bad", [True, "20", 8, 33, float("nan")])
def test_invalid_target(bad):
    with pytest.raises(CommandValidationError, match="invalid_temperature"):
        set_temperature(device(), temperature=bad)


def test_partial_range_uses_confirmed_other_bound_and_checks_deadband():
    assert (
        set_temperature(device(mode="HEATCOOL"), low=19)[0].params["coolCelsius"] == 24
    )
    with pytest.raises(CommandValidationError, match="invalid_range"):
        set_temperature(device(mode="HEATCOOL"), low=24, high=24.5)


def test_eco_does_not_silently_exit_for_temperature_changes():
    d = replace(device(), eco_mode="MANUAL_ECO")
    with pytest.raises(CommandValidationError, match="eco_active"):
        set_temperature(d, temperature=20)
    assert [x.name for x in set_mode(d, "COOL")] == [
        "ThermostatEco.SetMode",
        "ThermostatMode.SetMode",
    ]
    with pytest.raises(CommandValidationError, match="thermostat_off"):
        set_eco(device(mode="OFF"), True)


def test_fan_timer_is_explicit_and_auto_stops_only_manual_timer():
    assert set_fan(device(), "on", 15)[0].params == {
        "timerMode": "ON",
        "duration": "900s",
    }
    assert set_fan(device(), "auto", 15)[0].params == {"timerMode": "OFF"}
