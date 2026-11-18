# Google Nest Thermostat (Polling)

A Home Assistant custom integration for Nest thermostats.

## Features

- Native climate entities: Heat, Cool, Heat/Cool, Off, target temperatures, current HVAC activity and humidity.
- Eco presets and timed manual fan control, when the device exposes those capabilities. Fan `on` starts a timer (15 minutes by default); `auto` cancels the manual timer and leaves automatic HVAC fan operation available.
- Separate temperature, humidity and fan-timer-end sensors.
- One shared device-list request every **30 seconds**, configurable from 30–900 seconds. Changes made on the thermostat or in Google Home appear after the next successful poll; Google's REST data can also have its own delay.
- Automatic token refresh, refresh-token rotation persistence, one refresh/retry for HTTP 401, reauthentication prompts, quota guards, and backoff on service errors/rate limits.
- Before changing a thermostat, read its actual state; after a command, read the state back. Requested values are never reported as confirmed before Google returns them.
- Offline, removed or unreadable devices become unavailable. New shared thermostats are discovered during subsequent polls.
- English and Simplified Chinese configuration and error messages; redacted diagnostics.
- Bundled Google Nest icons and logos, including high-resolution images. After updating through HACS, restart Home Assistant to load them.

## Requirements

- Home Assistant **2026.10.4 or newer**, running Python 3.14.2 or newer.
- A supported thermostat, a Google Cloud project with **Smart Device Management API enabled**, a Web OAuth client and a **Nest Device Access project**. Google's Device Access registration requirements and fees still apply.
- A Google refresh token issued to that client with the `https://www.googleapis.com/auth/sdm.service` scope and permission for your thermostat.

Events may remain disabled in the Device Access Console. A Google Cloud project name and a Device Access project ID are different: this integration needs the **Device Access UUID**.

## Installation

### Manual

1. Copy `custom_components/google_nest_thermostat` to `/config/custom_components/google_nest_thermostat`.
2. Restart Home Assistant.
3. Open **Settings → Devices & services → Add integration → Google Nest Thermostat (Polling)**.
4. Enter the Device Access project ID, OAuth client ID, client secret and refresh token.

### HACS / Marketplace

When this repository is published, add its URL as a custom **Integration** repository, install it, restart Home Assistant, then add the integration as above. No external Python requirements are installed by this component.

## Get a refresh token

You can reuse an existing valid SDM refresh token **with its original OAuth client ID and secret**, or authorize a connection locally with the included helper. Do not paste credentials into chat, issues or logs.

1. In Google Cloud's Web OAuth client settings, add `http://localhost:8765/callback` as an authorized redirect URI. Download that client's JSON into `.local/client_secret.json`. Bind the same client ID to your Device Access project.
2. Run on the computer where your browser is signed in:

   ```sh
   python3 scripts/authorize.py \
     --client-secrets .local/client_secret.json \
     --project-id YOUR_DEVICE_ACCESS_UUID
   ```

3. The local helper opens the Google Partner Connections flow. Select your Google account, allow home information, and select your thermostat. It requests **SDM access only**.
4. After consent, the helper validates `devices.list` and saves the four setup fields to `.local/nest_credentials.json` with owner-only permissions. Open that private file locally and enter its fields in HA. Tokens and authorization codes are never printed.

The callback listens only on `127.0.0.1`, validates OAuth state, closes after authorization and refuses to overwrite an existing credentials file. Do not launch the helper on a remote NAS unless the browser can reach its loopback callback. `--port` and `--output` are available; register the matching redirect URI if changing the port.

Keep the Google OAuth consent screen's publishing/test-user configuration appropriate for your account. Google can revoke refresh tokens, including short-lived test-mode grants; the integration will ask for reauthentication when this happens. Reauthorize with the same account and enter a replacement refresh token; an optional replacement secret is supported.

## Options and control behavior

In the integration's **Configure** dialog, set the polling interval and manual fan duration. Reloading applies changes and cancels the old polling schedule.

Google currently permits 5 `devices.list` requests/minute and 10 `devices.get` requests/minute per project/user. A 30-second poll consumes 2 list requests/minute across all entities. Commands and their readbacks use the separate get/command quotas. Rapid manual actions can still reach Google's limits; the integration returns a clear error and delays retrying instead of queuing old thermostat commands.

- Internal temperatures and all SDM command payloads use **Celsius**. Home Assistant converts display/service temperatures for Fahrenheit installations. Changing the thermostat's own display unit does not change the API's Celsius contract.
- `HEATCOOL` maps to HA `heat_cool`, not `auto`. Set heating/cooling bounds with at least a 1.5°C gap.
- A temperature change while **OFF** is rejected unless the service call explicitly includes a supported heating/cooling mode. Reading temperatures never changes the mode.
- Exit the **Eco** preset before setting a target. Selecting a normal HVAC mode exits manual Eco as part of that explicit mode selection.
- The integration controls thermostats only. Camera events, doorbells, additional room sensors not exposed by SDM, firmware updates and LAN control are outside its scope.

## Switching from the official Nest integration

This is a separate domain, `google_nest_thermostat`. It does not modify the built-in `nest` component, its library or its configuration. Disable the old thermostat integration after validating the new one to avoid duplicate controls or competing automations. Keep it enabled if you still need its camera/event features, with care to target the intended thermostat entity.

Entity IDs and history are **not migrated automatically**. If you want to keep existing automation references/history, back up HA, release the old entity IDs and assign those IDs to the new entities in HA's entity settings. Existing topic/subscription resources are not deleted by this integration; remove them yourself only when no other consumers require them.

## Development

```sh
uv sync --locked --python 3.14
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python -m compileall -q custom_components scripts tests
uv run --locked pytest
uv run --locked python scripts/check_metadata.py
```

Tests run against a real Home Assistant test runtime with fake Google responses; network access is disabled. They cover scheduled shared polling, entity state propagation, control/readback, auth rotation/concurrency, bounded retries, rate limits/backoff, offline/removal recovery, discovery, setup/reauth/options, Fahrenheit conversion and diagnostic redaction. They do not operate a physical thermostat.

## References

- [SDM thermostat guide](https://developers.google.com/nest/device-access/api/thermostat)
- [Google OAuth authorization](https://developers.google.com/nest/device-access/authorize)
- [Google rate limits](https://developers.google.com/nest/device-access/project/limits)

MIT license. Independent project; not affiliated with Google.
