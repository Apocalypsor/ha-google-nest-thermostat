"""Constants for the Google Nest thermostat integration."""

from homeassistant.const import Platform

DOMAIN = "google_nest_thermostat"
PLATFORMS = [Platform.CLIMATE, Platform.SENSOR]

CONF_PROJECT_ID = "project_id"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_ACCESS_TOKEN = "access_token"
CONF_EXPIRES_AT = "expires_at"
CONF_ACCOUNT_ID = "account_id"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_FAN_DURATION = "fan_duration"

API_BASE_URL = "https://smartdevicemanagement.googleapis.com/v1"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DEFAULT_SCAN_INTERVAL = 30
MIN_SCAN_INTERVAL = 30
MAX_SCAN_INTERVAL = 900
DEFAULT_FAN_DURATION = 15  # Minutes, used by fan_mode=on.
MAX_BACKOFF = 900
REQUEST_TIMEOUT = 20
REFRESH_MARGIN = 90
MIN_TEMP_C = 9.0
MAX_TEMP_C = 32.0
MIN_RANGE_C = 1.5

TRAIT_PREFIX = "sdm.devices.traits."
COMMAND_PREFIX = "sdm.devices.commands."
