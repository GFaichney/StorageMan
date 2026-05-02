from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "google_drive": {
        "service_account_json": "",
        "client_id": "",
        "client_secret": "",
        "refresh_token": "",
    },
    "dropbox": {
        "access_token": "",
    },
    "sync": {
        "max_threads": 5,
    },
}


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return DEFAULT_CONFIG.copy()

    try:
        loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return DEFAULT_CONFIG.copy()

    merged = DEFAULT_CONFIG.copy()
    for section, values in loaded.items():
        if section in merged and isinstance(values, dict):
            merged[section] = {**merged[section], **values}
    return merged


def save_config(config: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
