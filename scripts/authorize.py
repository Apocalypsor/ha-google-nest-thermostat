#!/usr/bin/env python3
"""Obtain an SDM refresh token locally, without publishing or subscribing."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
from uuid import UUID

SDM_SCOPE = "https://www.googleapis.com/auth/sdm.service"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def authorization_url(project: str, client_id: str, redirect: str, state: str) -> str:
    project = str(UUID(project))
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": SDM_SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )
    return f"https://nestservices.google.com/partnerconnections/{project}/auth?{query}"


def callback_code(path: str, expected_state: str) -> str:
    """Validate a callback without logging its URL or query string."""
    parsed = urlsplit(path)
    query = parse_qs(parsed.query)
    if parsed.path != "/callback" or not hmac.compare_digest(
        query.get("state", [""])[0], expected_state
    ):
        raise ValueError("Invalid callback state")
    if query.get("error") or not query.get("code", [""])[0]:
        raise ValueError("Google authorization was not completed")
    return query["code"][0]


def write_credentials(path: Path, data: dict[str, str]) -> None:
    """Create a private file exclusively; never overwrite existing credentials."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def google_json(
    url: str, *, form: dict[str, str] | None = None, token: str | None = None
) -> dict[str, Any]:
    req = Request(url, data=urlencode(form).encode() if form else None)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urlopen(req, timeout=30) as response:
            result = json.load(response)
    except HTTPError as err:
        raise RuntimeError(f"Google request failed (HTTP {err.code})") from None
    except URLError, TimeoutError, ValueError:
        raise RuntimeError("Could not read Google response") from None
    if not isinstance(result, dict):
        raise RuntimeError("Invalid Google response")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--client-secrets",
        type=Path,
        required=True,
        help="Web OAuth client JSON downloaded from Google Cloud",
    )
    parser.add_argument("--project-id", required=True, help="Nest Device Access UUID")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--output", type=Path, default=Path(".local/nest_credentials.json")
    )
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose another private output file")
    try:
        project = str(UUID(args.project_id))
        document = json.loads(args.client_secrets.read_text())
        client = document["web"]
        client_id, client_secret = client["client_id"], client["client_secret"]
    except ValueError, KeyError, OSError, TypeError:
        parser.error("Provide a valid Device Access UUID and Web OAuth client JSON")
    redirect = f"http://localhost:{args.port}/callback"
    if redirect not in client.get("redirect_uris", []):
        parser.error(
            f"Register {redirect} on this Web OAuth client and download its JSON again"
        )
    state = secrets.token_urlsafe(32)
    start_url = authorization_url(project, client_id, redirect, state)
    result: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            pass

        def do_GET(self) -> None:
            if self.path == "/":
                self.send_response(302)
                self.send_header("Location", start_url)
                self.end_headers()
                return
            try:
                code = callback_code(self.path, state)
            except ValueError:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Authorization not accepted. Return to the terminal.")
            else:
                result["code"] = code
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b"Authorization received. Return to the terminal.")

    try:
        with HTTPServer(("127.0.0.1", args.port), CallbackHandler) as server:
            server.timeout = 1
            print(f"Open http://localhost:{args.port}/ in this computer's browser.")
            print(
                "Select your Google account and thermostat. "
                "Credentials will not be printed."
            )
            webbrowser.open(f"http://localhost:{args.port}/")
            end = time.monotonic() + 300
            while "code" not in result and time.monotonic() < end:
                server.handle_request()
        if "code" not in result:
            raise RuntimeError("Authorization timed out; no credentials saved")
        tokens = google_json(
            TOKEN_URL,
            form={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": result.pop("code"),
                "redirect_uri": redirect,
                "grant_type": "authorization_code",
            },
        )
        refresh, access = tokens.get("refresh_token"), tokens.get("access_token")
        if not isinstance(refresh, str) or not refresh or not isinstance(access, str):
            raise RuntimeError(
                "Google did not return a refresh token; authorize again with consent"
            )
        devices = google_json(
            f"https://smartdevicemanagement.googleapis.com/v1/enterprises/{project}/devices",
            token=access,
        ).get("devices", [])
        if not isinstance(devices, list):
            raise RuntimeError("Invalid Google device list; no credentials saved")
        count = sum(
            isinstance(d, dict)
            and "sdm.devices.traits.ThermostatMode" in d.get("traits", {})
            for d in devices
        )
        if count == 0:
            raise RuntimeError(
                "No shared thermostats found; "
                "select a thermostat in Partner Connections"
            )
        write_credentials(
            args.output,
            {
                "project_id": project,
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh,
            },
        )
        print(
            f"Verified {count} thermostat(s). "
            f"Credentials saved privately to {args.output}."
        )
        print(
            "Enter the four fields in Home Assistant. "
            "Do not post this file or commit it."
        )
    except (OSError, RuntimeError) as err:
        parser.exit(1, f"{err}\n")


if __name__ == "__main__":
    main()
