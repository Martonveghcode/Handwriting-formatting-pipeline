from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from mtyh.logic.auto_pipeline import pdf_stem, validate_file_component
from mtyh.logic.config_store import deep_merge, default_config
from mtyh.logic.image_tools import ExportMode, ImageProcessingSettings
from mtyh.logic.text_formatter import (
    REPLACEMENT_KEY_TO_RESULT,
    REPLACEMENT_RESULT_TO_KEY,
    FormatterSettings,
    normalize_character_map,
)

FORMATTER_RANGES = {
    "min_words": (1, 50),
    "max_words": (1, 80),
    "target_width": (10, 200),
    "tolerance": (0, 50),
    "lines_per_page": (0, 200),
}
IMAGE_RANGES = {"line_thickness": (1, 80), "y_tolerance": (1, 80), "overlap_px": (0, 5000)}
IMAGE_FIELDS = (*IMAGE_RANGES, "line_color", "remove_yellow", "connect_lines")
RANGES = {
    **FORMATTER_RANGES,
    **IMAGE_RANGES,
    "width": (980, 3840),
    "height": (660, 2160),
}
CHOICES = {
    "replacement_direction": {REPLACEMENT_RESULT_TO_KEY, REPLACEMENT_KEY_TO_RESULT},
    "default_tab": {"single", "stitch"},
    "export_mode": {mode.value for mode in ExportMode},
    "theme": {"system", "light", "dark", "custom"},
    "palette_family": {"All colours", "Blue", "Green", "Purple", "Warm", "Neutral"},
}


def public_settings(config: dict[str, Any]) -> dict[str, Any]:
    formatter = dict(config["formatter"])
    pairs = normalize_character_map(formatter.get("character_map", formatter.get("replacements")))
    formatter["character_map"] = [{"source": source, "result": result} for source, result in pairs]
    formatter.pop("replacements", None)
    image = config["image"]
    single = image.get("single", image)
    stitch = image.get("stitch", image)
    defaults = default_config()["image"]
    return {
        "formatter": formatter,
        "image": {
            "stitch": {key: stitch.get(key, defaults[key]) for key in IMAGE_FIELDS},
            "single": {key: single.get(key, defaults[key]) for key in IMAGE_FIELDS if key != "overlap_px"},
            **{key: image.get(key, defaults[key]) for key in defaults if key not in IMAGE_FIELDS},
            "single_output_name": image.get("single_output_name", ""),
        },
        **{key: config[key] for key in ("output_dir", "auto", "appearance", "hotkeys", "window")},
    }


def _validate_pair_list(value: list, label: str) -> None:
    if not value:
        raise ValueError(f"{label} must contain at least one mapping.")
    sources = set()
    for pair in value:
        if not isinstance(pair, dict) or set(pair) != {"source", "result"}:
            raise ValueError(f"{label} entries require source and result.")
        if any(not isinstance(char, str) or len(char) != 1 or not char.isprintable() or char.isspace()
               for char in pair.values()):
            raise ValueError(f"{label} entries must map one printable, non-space character to another.")
        if pair["source"] in sources:
            raise ValueError(f"{label} repeats source {pair['source']!r}.")
        sources.add(pair["source"])


def output_directory(value: str, label: str) -> str:
    path = Path(value)
    if not path.is_absolute() or not re.fullmatch("[A-Za-z]:", path.drive):
        raise ValueError(f"{label} must be an absolute Windows drive path.")
    for component in path.parts[1:]:
        if component not in {".", ".."}:
            validate_file_component(component, label)
    return str(path.resolve())


def _validate_updates(updates: dict, shape: dict, prefix: str = "") -> None:
    for key, value in updates.items():
        label = f"{prefix}{key}"
        if key not in shape:
            raise ValueError(f"Unknown setting {label}.")
        if key in {"x", "y"}:
            if value is not None and type(value) is not int:
                raise ValueError(f"{label} must be an integer or null.")
            continue
        expected = shape[key]
        if type(value) is not type(expected):
            raise ValueError(f"{label} must have type {type(expected).__name__}.")
        if isinstance(value, str):
            value.encode("utf-8")
            if any(ord(char) < 32 for char in value):
                raise ValueError(f"{label} cannot contain control characters.")
        if isinstance(value, dict):
            _validate_updates(value, expected, f"{label}.")
        elif key in RANGES:
            low, high = RANGES[key]
            if not low <= value <= high:
                raise ValueError(f"{label} must be between {low} and {high}.")
        elif key in CHOICES and value not in CHOICES[key]:
            raise ValueError(f"{label} must be one of {', '.join(sorted(CHOICES[key]))}.")
        elif key == "line_color" and (len(value) != 3 or any(type(c) is not int or not 0 <= c <= 255 for c in value)):
            raise ValueError(f"{label} must be an RGB array of three integers between 0 and 255.")
        elif key == "character_map":
            _validate_pair_list(value, label)
        elif key in {"image_output_dir", "pdf_output_dir", "output_dir", "import_dir"}:
            output_directory(value, label)
        elif key in {"hst_dir", "line_graph_dir"} and not value.startswith(("/", "~/")):
            raise ValueError(f"{label} must be an absolute Linux path or start with ~/.")
        elif key in {"wsl_distro", "quick_copy"} and not value.strip():
            raise ValueError(f"{label} must contain a value.")
        elif key.startswith("custom_") and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError(f"{label} must use #RRGGBB hex syntax.")
        elif key in {"image_prefix", "pdf_name", "output_name", "single_output_name"} and value:
            if key == "pdf_name":
                pdf_stem(value)
            else:
                validate_file_component(value, label)


def apply_settings_patch(config: dict[str, Any], updates: dict) -> dict[str, Any]:
    _validate_updates(updates, public_settings(default_config()))
    result = deep_merge(config, updates)
    formatter = result["formatter"]
    if updates.get("formatter"):
        if formatter["min_words"] > formatter["max_words"]:
            raise ValueError("formatter.min_words cannot be greater than formatter.max_words.")
        if "character_map" in updates["formatter"]:
            formatter["replacements"] = {pair["source"]: pair["result"] for pair in formatter["character_map"]}
    for tab in ("stitch", "single"):
        edits = updates.get("image", {}).get(tab)
        if edits:
            current = public_settings(config)["image"][tab]
            result["image"][tab] = deep_merge(current, edits)
            if tab == "stitch":
                result["image"].update(result["image"][tab])
    return result


def formatter_inputs(settings: dict[str, Any]) -> tuple[FormatterSettings, list[tuple[str, str]], str]:
    formatter = settings["formatter"]
    if formatter["min_words"] > formatter["max_words"]:
        raise ValueError("formatter.min_words cannot be greater than formatter.max_words.")
    rules = FormatterSettings(**{key: formatter[key] for key in FORMATTER_RANGES})
    return rules, normalize_character_map(formatter["character_map"]), formatter["replacement_direction"]


def stitch_settings(settings: dict[str, Any]) -> ImageProcessingSettings:
    fields = dict(settings["image"]["stitch"])
    fields["line_color"] = tuple(fields["line_color"])
    return ImageProcessingSettings(**fields)
