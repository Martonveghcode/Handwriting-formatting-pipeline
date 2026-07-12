from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "MTYH"
ORG_NAME = "MTYH"


def app_data_dir() -> Path:
    root = os.getenv("APPDATA")
    if root:
        return Path(root) / APP_NAME
    return Path.home() / "AppData" / "Roaming" / APP_NAME


def logs_dir() -> Path:
    return app_data_dir() / "logs"


def config_path() -> Path:
    return app_data_dir() / "config.json"


def default_output_dir() -> Path:
    return Path.home() / "Desktop" / "for printing"


def resource_path(*parts: str) -> Path:
    if hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS) / "mtyh"
    else:
        base = Path(__file__).resolve().parent
    return base.joinpath(*parts)


def icon_path() -> Path:
    return resource_path("resources", "text formater.ico")
