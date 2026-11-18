"""Temperature, humidity and fan timer sensors using shared REST snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NestConfigEntry
from .const import TRAIT_PREFIX
from .coordinator import NestCoordinator
from .entity import NestEntity


@dataclass(frozen=True, kw_only=True)
class NestSensorDescription(SensorEntityDescription):
    trait: str


DESCRIPTIONS = (
    NestSensorDescription(
        key="temperature",
        translation_key="temperature",
        trait="Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    NestSensorDescription(
        key="humidity",
        translation_key="humidity",
        trait="Humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    NestSensorDescription(
        key="fan_timeout",
        translation_key="fan_timeout",
        trait="Fan",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NestConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    known: set[tuple[str, str]] = set()

    @callback
    def add_new() -> None:
        entities = []
        for name, device in coordinator.data.thermostats.items():
            for description in DESCRIPTIONS:
                key = (name, description.key)
                if (
                    key not in known
                    and TRAIT_PREFIX + description.trait in device.trait_names
                ):
                    known.add(key)
                    entities.append(NestSensor(coordinator, name, description))
        async_add_entities(entities)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class NestSensor(NestEntity, SensorEntity):
    entity_description: NestSensorDescription

    def __init__(
        self,
        coordinator: NestCoordinator,
        name: str,
        description: NestSensorDescription,
    ) -> None:
        super().__init__(coordinator, name)
        self.entity_description = description
        self._attr_unique_id = f"{name}-{description.key}"

    @property
    def native_value(self) -> float | datetime | None:
        if self.entity_description.key == "fan_timeout":
            return (
                self.thermostat.fan_timeout
                if self.thermostat.fan_timer_mode == "ON"
                else None
            )
        return getattr(self.thermostat, self.entity_description.key)
