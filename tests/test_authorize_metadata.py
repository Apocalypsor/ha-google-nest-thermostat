"""Verify the local authorization helper without authorizing any account."""

import json
import stat
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts.authorize import (
    SDM_SCOPE,
    authorization_url,
    callback_code,
    write_credentials,
)
from scripts.check_metadata import main as check_metadata

from .conftest import PROJECT


def test_authorization_requests_only_sdm_and_validates_project():
    url = authorization_url(
        PROJECT, "test-client", "http://localhost:8765/callback", "test-state"
    )
    parsed = urlsplit(url)
    assert parsed.netloc == "nestservices.google.com"
    query = parse_qs(parsed.query)
    assert query["scope"] == [SDM_SCOPE]
    assert query["state"] == ["test-state"] and query["access_type"] == ["offline"]
    assert "pubsub" not in url
    with pytest.raises(ValueError):
        authorization_url("invalid/project", "client", "local", "state")


@pytest.mark.parametrize(
    "path",
    [
        "/callback?code=test-code&state=wrong",
        "/callback?code=test-code",
        "/other?state=correct&code=test-code",
        "/callback?state=correct&error=access_denied",
    ],
)
def test_invalid_oauth_callback_rejected(path):
    with pytest.raises(ValueError):
        callback_code(path, "correct")


def test_good_callback():
    assert (
        callback_code("/callback?state=correct&code=test-code", "correct")
        == "test-code"
    )


def test_credential_file_private_and_never_overwritten(tmp_path):
    path = tmp_path / "private/credentials.json"
    write_credentials(path, {"refresh_token": "unit-test-placeholder"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        write_credentials(path, {})
    assert json.loads(path.read_text()) == {"refresh_token": "unit-test-placeholder"}


def test_integration_metadata_and_translations():
    check_metadata()
