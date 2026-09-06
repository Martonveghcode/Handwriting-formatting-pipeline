from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from mtyh.logic.text_formatter import (
    REPLACEMENT_RESULT_TO_KEY,
    normalize_replacements,
)
from mtyh.paths import config_path, default_output_dir

LOGGER = logging.getLogger(__name__)


def default_config() -> dict[str, Any]:
    desktop = Path.home() / "moodle-proxy" / "Desktop"
    if not desktop.is_dir():
        desktop = Path.home() / "Desktop"
    return {
        "output_dir": str(default_output_dir()),
        "formatter": {
            "min_words": 7,
            "max_words": 10,
            "target_width": 54,
            "tolerance": 4,
            "lines_per_page": 33,
            "replacement_direction": REPLACEMENT_RESULT_TO_KEY,
            "replacements": normalize_replacements(),
        },
        "image": {
            "import_dir": str(Path.home()),
            "line_thickness": 7,
            "y_tolerance": 2,
            "line_color": [0, 0, 0],
            "overlap_px": 0,
            "remove_yellow": True,
            "connect_lines": True,
            "default_tab": "stitch",
            "export_mode": "pdf",
            "output_name": "handwriting.pdf",
            "delete_originals": False,
            "confirm_delete_originals": True,
        },
        "hotkeys": {
            "quick_copy": "ctrl+alt+c",
            "quick_copy_enabled": False,
        },
        "macro": {
            "prefix": "newtrainingdata",
            "counter": 161,
            "initial_delay_seconds": 2.0,
            "toggle_hotkey": "F8",
            "step_hotkey": "F9",
        },
        "auto": {
            "image_prefix": "",
            "pdf_name": "",
            "image_output_dir": str(desktop / "output images" / "AUTO"),
            "pdf_output_dir": str(desktop / "for printing" / "AUTO"),
            "wsl_distro": "Ubuntu",
            "hst_dir": "/home/marton/helit/handwriting/hst",
            "line_graph_dir": "/home/marton/trainingdata",
            "terminate_wsl": True,
        },
        "window": {
            "width": 1280,
            "height": 900,
            "x": None,
            "y": None,
        },
        "appearance": {
            "theme": "system",
            "palette_family": "All colours",
            "custom_background": "#f4f7fb",
            "custom_surface": "#ffffff",
            "custom_text": "#172033",
            "custom_accent": "#315efb",
        },
    }


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_path()

    def load(self) -> dict[str, Any]:
        defaults = default_config()
        if not self.path.exists():
            return defaults
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Could not load config from %s: %s", self.path, exc)
            return defaults
        if not isinstance(data, dict):
            LOGGER.warning("Config at %s is not an object; using defaults", self.path)
            return defaults
        return deep_merge(defaults, data)

    def save(self, config: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.path)
        LOGGER.info("Saved config to %s", self.path)
