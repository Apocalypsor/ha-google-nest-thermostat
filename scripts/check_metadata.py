"""Validate integration metadata, translation parity and forbidden transports."""

from __future__ import annotations

import ast
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/google_nest_thermostat"


def leaves(data: object, prefix: str = "") -> set[str]:
    if isinstance(data, dict):
        return {
            key for k, value in data.items() for key in leaves(value, prefix + "." + k)
        }
    return {prefix}


def main() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["domain"] == COMPONENT.name
    assert manifest["iot_class"] == "cloud_polling"
    assert manifest["requirements"] == []
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert hacs["homeassistant"] == "2026.10.4"
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert manifest["version"] == project["project"]["version"], (
        f"Version mismatch: manifest.json={manifest['version']}, "
        f"pyproject.toml={project['project']['version']}. "
        "Update both versions and regenerate uv.lock with uv lock."
    )
    strings = json.loads((COMPONENT / "strings.json").read_text())
    for language in ("en", "zh-Hans"):
        translated = json.loads(
            (COMPONENT / f"translations/{language}.json").read_text()
        )
        assert leaves(translated) == leaves(strings), language
    for source in COMPONENT.glob("*.py"):
        text = source.read_text()
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            assert not any(
                "pubsub" in name or "google_nest_sdm" in name for name in modules
            ), source
        assert "pubsub.googleapis.com" not in text, source
    print("Metadata, translation keys and polling-only dependency checks passed.")


if __name__ == "__main__":
    main()
