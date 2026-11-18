"""Support data containing no credentials, account IDs, names or device paths."""

from homeassistant.core import HomeAssistant

from . import NestConfigEntry
from .const import CONF_FAN_DURATION, DEFAULT_FAN_DURATION


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NestConfigEntry
) -> dict:
    coordinator = entry.runtime_data.coordinator
    devices = coordinator.data.thermostats.values() if coordinator.data else []
    return {
        "integration_version": "0.1.0",
        "transport": "sdm_rest_polling",
        "poll_interval_seconds": coordinator.poll_seconds,
        "current_interval_seconds": coordinator.update_interval.total_seconds(),
        "last_update_success": coordinator.last_update_success,
        "fan_timer_minutes": entry.options.get(CONF_FAN_DURATION, DEFAULT_FAN_DURATION),
        "thermostats": [
            {
                "index": index,
                "connectivity": device.connectivity,
                "mode": device.mode,
                "available_modes": list(device.available_modes),
                "hvac_status": device.hvac_status,
                "temperature_celsius": device.temperature,
                "humidity_percent": device.humidity,
                "eco_mode": device.eco_mode,
                "fan_timer_mode": device.fan_timer_mode,
            }
            for index, device in enumerate(devices, 1)
        ],
    }
