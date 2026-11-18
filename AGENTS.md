# Google Nest Thermostat (Polling)

- This integration is cloud polling only. Never add a Pub/Sub client, subscriber, event-binding requirement, or dependency on the built-in Nest integration.
- Use the documented Google SDM REST API and OAuth refresh endpoint. Do not use browser cookies or undocumented Nest APIs.
- Keep network/authentication in `api.py`, immutable parsing in `models.py`, validation in `commands.py`, and HA state distribution in `coordinator.py`.
- All API temperatures are Celsius. `HEATCOOL` maps to HA `heat_cool`. Do not silently switch an OFF thermostat on or exit Eco merely to change its target.
- Serialize controls, validate against fresh state, reserve an action's complete quota before its first write, and read confirmed state back. Do not fabricate device events or optimistic readings.
- Share the full-device poll across entities. Single-device control readbacks must not postpone other devices' polls.
- Token refresh is serialized; persist any rotated refresh token before using its access token. A 401 allows one refresh/retry only. Never log credentials or raw authentication responses.
- Diagnostics must omit credentials, account/device identifiers, names and addresses. Never commit credential downloads or live response captures.
- Keep English and Simplified Chinese translation keys in sync. Support the minimum HA version in `hacs.json`.
- Verify with `uv sync --locked`, `uv run --locked ruff check .`, `uv run --locked ruff format --check .`, `uv run --locked pytest`, `uv run --locked python scripts/check_metadata.py`, and a secret scan before handoff.
- Tests must use fake Google responses. Do not operate a physical thermostat in automated tests.
