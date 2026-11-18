"""Availability and identity shared by thermostat entities."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NestCoordinator
from .models import NestThermostat


class NestEntity(CoordinatorEntity[NestCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: NestCoordinator, resource_name: str) -> None:
        super().__init__(coordinator)
        self.resource_name = resource_name
        self._last_device = coordinator.data.thermostats[resource_name]

    @property
    def thermostat(self) -> NestThermostat:
        if (
            self.coordinator.data is not None
            and (device := self.coordinator.data.thermostats.get(self.resource_name))
            is not None
        ):
            self._last_device = device
        return self._last_device

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and self.resource_name in self.coordinator.data.thermostats
            and self.thermostat.is_online
        )

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.resource_name)},
            name=self.thermostat.display_name,
            manufacturer="Google Nest",
            model="Thermostat",
            suggested_area=self.thermostat.room_name,
        )
