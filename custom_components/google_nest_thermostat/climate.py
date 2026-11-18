"""Native HA climate entities for polled Nest thermostats."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ATTR_TEMPERATURE,
    FAN_AUTO,
    FAN_ON,
    PRESET_ECO,
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NestConfigEntry, commands
from .const import (
    CONF_FAN_DURATION,
    DEFAULT_FAN_DURATION,
    DOMAIN,
    MAX_TEMP_C,
    MIN_TEMP_C,
    TRAIT_PREFIX,
)
from .coordinator import NestCoordinator
from .entity import NestEntity

TO_HA = {
    "HEAT": HVACMode.HEAT,
    "COOL": HVACMode.COOL,
    "HEATCOOL": HVACMode.HEAT_COOL,
    "OFF": HVACMode.OFF,
}
TO_SDM = {v: k for k, v in TO_HA.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NestConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    known: set[str] = set()

    @callback
    def add_new() -> None:
        new = set(coordinator.data.thermostats) - known
        known.update(new)
        async_add_entities(NestClimate(coordinator, key) for key in sorted(new))

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class NestClimate(NestEntity, ClimateEntity):
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_min_temp = MIN_TEMP_C
    _attr_max_temp = MAX_TEMP_C

    def __init__(self, coordinator: NestCoordinator, resource_name: str) -> None:
        super().__init__(coordinator, resource_name)
        self._attr_unique_id = resource_name
        self._last_mode: HVACMode | None = None

    @property
    def supported_features(self) -> ClimateEntityFeature:
        device = self.thermostat
        features = ClimateEntityFeature(0)
        if "OFF" in device.available_modes:
            features |= ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON
        if TRAIT_PREFIX + "ThermostatTemperatureSetpoint" in device.trait_names:
            if "HEATCOOL" in device.available_modes:
                features |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
            if any(mode in device.available_modes for mode in ("HEAT", "COOL")):
                features |= ClimateEntityFeature.TARGET_TEMPERATURE
        if device.eco_mode is not None:
            features |= ClimateEntityFeature.PRESET_MODE
        if device.fan_timer_mode is not None:
            features |= ClimateEntityFeature.FAN_MODE
        return features

    @property
    def hvac_modes(self) -> list[HVACMode]:
        return [TO_HA[m] for m in self.thermostat.available_modes if m in TO_HA]

    @property
    def hvac_mode(self) -> HVACMode | None:
        return TO_HA.get(self.thermostat.mode)

    @property
    def hvac_action(self) -> HVACAction | None:
        if self.thermostat.mode == "OFF":
            return HVACAction.OFF
        return {
            "HEATING": HVACAction.HEATING,
            "COOLING": HVACAction.COOLING,
            "OFF": HVACAction.IDLE,
        }.get(self.thermostat.hvac_status)

    @property
    def current_temperature(self) -> float | None:
        value = self.thermostat.temperature
        return round(value, 1) if value is not None else None

    @property
    def current_humidity(self) -> float | None:
        return self.thermostat.humidity

    @property
    def target_temperature(self) -> float | None:
        device = self.thermostat
        if device.mode == "HEAT":
            return device.eco_heat if device.is_eco else device.heat_setpoint
        if device.mode == "COOL":
            return device.eco_cool if device.is_eco else device.cool_setpoint
        return None

    @property
    def target_temperature_low(self) -> float | None:
        device = self.thermostat
        if device.mode != "HEATCOOL":
            return None
        return device.eco_heat if device.is_eco else device.heat_setpoint

    @property
    def target_temperature_high(self) -> float | None:
        device = self.thermostat
        if device.mode != "HEATCOOL":
            return None
        return device.eco_cool if device.is_eco else device.cool_setpoint

    @property
    def preset_modes(self) -> list[str] | None:
        return (
            [PRESET_NONE, PRESET_ECO] if self.thermostat.eco_mode is not None else None
        )

    @property
    def preset_mode(self) -> str | None:
        if self.thermostat.eco_mode is None:
            return None
        return PRESET_ECO if self.thermostat.is_eco else PRESET_NONE

    @property
    def fan_modes(self) -> list[str] | None:
        return (
            [FAN_AUTO, FAN_ON] if self.thermostat.fan_timer_mode is not None else None
        )

    @property
    def fan_mode(self) -> str | None:
        if self.thermostat.fan_timer_mode is None:
            return None
        return FAN_ON if self.thermostat.fan_timer_mode == "ON" else FAN_AUTO

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        mode = TO_SDM.get(hvac_mode)
        if mode is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="unsupported_mode"
            )
        previous_mode = self.hvac_mode
        await self.coordinator.async_control(
            self.resource_name, lambda d: commands.set_mode(d, mode)
        )
        if previous_mode is not None and previous_mode != HVACMode.OFF:
            self._last_mode = previous_mode

    async def async_set_temperature(self, **kwargs: Any) -> None:
        requested = kwargs.get(ATTR_HVAC_MODE)
        mode = TO_SDM.get(requested) if requested is not None else None
        if requested is not None and mode is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="unsupported_mode"
            )
        await self.coordinator.async_control(
            self.resource_name,
            lambda d: commands.set_temperature(
                d,
                temperature=kwargs.get(ATTR_TEMPERATURE),
                low=kwargs.get(ATTR_TARGET_TEMP_LOW),
                high=kwargs.get(ATTR_TARGET_TEMP_HIGH),
                mode=mode,
            ),
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode not in (PRESET_ECO, PRESET_NONE):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="unsupported_preset"
            )
        await self.coordinator.async_control(
            self.resource_name, lambda d: commands.set_eco(d, preset_mode == PRESET_ECO)
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        duration = self.coordinator.entry.options.get(
            CONF_FAN_DURATION, DEFAULT_FAN_DURATION
        )
        await self.coordinator.async_control(
            self.resource_name, lambda d: commands.set_fan(d, fan_mode, duration)
        )

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_turn_on(self) -> None:
        mode = self._last_mode
        if mode not in self.hvac_modes or mode == HVACMode.OFF:
            mode = next((m for m in self.hvac_modes if m != HVACMode.OFF), None)
        if mode is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="unsupported_mode"
            )
        await self.async_set_hvac_mode(mode)
