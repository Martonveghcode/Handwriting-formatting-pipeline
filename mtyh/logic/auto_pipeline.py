from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from mtyh.logic.image_tools import ExportMode, ImageProcessingSettings, process_stitch_export
from mtyh.logic.text_formatter import (
    FormatterSettings,
    apply_replacements,
    build_replacement_mapping,
    format_text,
    split_formatted_sections,
)
from mtyh.paths import app_data_dir, resource_path

LOGGER = logging.getLogger(__name__)

ProgressCallback = Callable[[str], None]
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


@dataclass(frozen=True)
class AutoPipelineResult:
    pdf_path: Path
    page_count: int


def desktop_root() -> Path:
    proxied = Path.home() / "moodle-proxy" / "Desktop"
    return proxied if proxied.is_dir() else Path.home() / "Desktop"


def default_auto_image_dir() -> Path:
    return desktop_root() / "output images" / "AUTO"


def default_auto_pdf_dir() -> Path:
    return desktop_root() / "for printing" / "AUTO"


def validate_file_component(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"Enter a {label}.")
    if cleaned.endswith((" ", ".")) or INVALID_FILENAME_CHARS.search(cleaned):
        raise ValueError(f"The {label} contains characters Windows cannot use in a file name.")
    if cleaned.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        raise ValueError(f"The {label} is a reserved Windows file name.")
    return cleaned


def windows_to_wsl_path(path: Path) -> str:
    resolved = path.resolve()
    drive = resolved.drive
    if len(drive) == 2 and drive[1] == ":":
        remainder = resolved.as_posix()[3:]
        return f"/mnt/{drive[0].lower()}/{remainder}"
    raise ValueError(f"AUTO requires a file on a Windows drive, not {resolved}")


def prepare_sections(
    source_text: str,
    formatter_settings: FormatterSettings,
    character_pairs: Iterable[tuple[str, str]],
    replacement_direction: str,
) -> list[str]:
    mapping = build_replacement_mapping(character_pairs, replacement_direction)
    processed = apply_replacements(source_text, mapping)
    formatted, _ = format_text(processed, formatter_settings)
    sections = split_formatted_sections(formatted)
    if not sections:
        raise ValueError("The source text did not produce any sections.")
    return sections


def _progress_message(payload: dict[str, object]) -> str | None:
    stage = payload.get("stage")
    if stage == "starting":
        return f"Starting synthesis for {payload.get('sections')} page(s)…"
    if stage == "loading_examples":
        return f"Loading handwriting examples: {payload.get('current')} of {payload.get('total')}…"
    if stage == "generating":
        return f"Generating page {payload.get('current')} of {payload.get('total')}…"
    if stage == "generated":
        return f"Saved image {payload.get('current')} of {payload.get('total')}."
    if stage == "complete":
        return "Handwriting generation complete. Creating PDF…"
    if stage == "error":
        return f"Synthesis error: {payload.get('message')}"
    return None


def _run_synthesis(
    sections_file: Path,
    image_dir: Path,
    image_prefix: str,
    distro: str,
    hst_dir: str,
    line_graph_dir: str,
    progress_callback: ProgressCallback,
) -> None:
    script = resource_path("synthesis", "hst_batch.py")
    if not script.is_file():
        raise FileNotFoundError(f"The AUTO synthesis adapter is missing: {script}")

    command_parts = [
        f"cd {shlex.quote(hst_dir)}",
        "export OMP_NUM_THREADS=8 OMP_DYNAMIC=FALSE",
        "python3",
        shlex.quote(windows_to_wsl_path(script)),
        "--sections-json",
        shlex.quote(windows_to_wsl_path(sections_file)),
        "--output-dir",
        shlex.quote(windows_to_wsl_path(image_dir)),
        "--prefix",
        shlex.quote(image_prefix),
        "--line-graphs",
        shlex.quote(line_graph_dir),
    ]
    command = " && ".join(command_parts[:2]) + " && " + " ".join(command_parts[2:])
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        ["wsl.exe", "-d", distro, "--", "bash", "-lc", command],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creation_flags,
    )
    recent_output: list[str] = []
    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.rstrip()
        LOGGER.info("HST: %s", line)
        if line.startswith("MTYH_PROGRESS "):
            try:
                payload = json.loads(line.removeprefix("MTYH_PROGRESS "))
            except json.JSONDecodeError:
                continue
            message = _progress_message(payload)
            if message:
                progress_callback(message)
        elif line:
            recent_output.append(line)
            del recent_output[:-12]
    return_code = process.wait()
    if return_code:
        details = "\n".join(recent_output[-6:])
        suffix = f"\n\n{details}" if details else ""
        raise RuntimeError(f"Handwriting synthesis exited with code {return_code}.{suffix}")


def run_auto_pipeline(
    source_text: str,
    image_prefix: str,
    pdf_name: str,
    formatter_settings: FormatterSettings,
    character_pairs: Iterable[tuple[str, str]],
    replacement_direction: str,
    image_settings: ImageProcessingSettings,
    auto_config: dict[str, object] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> AutoPipelineResult:
    progress = progress_callback or (lambda _message: None)
    if not source_text.strip():
        raise ValueError("Paste source text before running AUTO.")
    image_prefix = validate_file_component(image_prefix, "generated image prefix")
    pdf_name = validate_file_component(pdf_name, "final PDF name")
    if pdf_name.casefold().endswith(".pdf"):
        pdf_name = pdf_name[:-4].rstrip()
        pdf_name = validate_file_component(pdf_name, "final PDF name")

    options = auto_config or {}
    image_dir = Path(str(options.get("image_output_dir") or default_auto_image_dir()))
    pdf_dir = Path(str(options.get("pdf_output_dir") or default_auto_pdf_dir()))
    distro = str(options.get("wsl_distro") or "Ubuntu")
    hst_dir = str(options.get("hst_dir") or "/home/marton/helit/handwriting/hst")
    line_graph_dir = str(options.get("line_graph_dir") or "/home/marton/trainingdata")
    terminate_wsl = bool(options.get("terminate_wsl", True))

    sections = prepare_sections(
        source_text,
        formatter_settings,
        character_pairs,
        replacement_direction,
    )
    image_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    image_paths = [image_dir / f"{image_prefix} {index}.png" for index in range(1, len(sections) + 1)]
    collisions = [path for path in image_paths if path.exists()]
    if collisions:
        raise FileExistsError(
            f"{collisions[0].name} already exists in the AUTO image folder. "
            "Move or rename the existing batch, or choose another prefix."
        )

    work_dir = app_data_dir() / "work" / uuid.uuid4().hex
    work_dir.mkdir(parents=True, exist_ok=False)
    sections_file = work_dir / "sections.json"
    sections_file.write_text(json.dumps(sections, ensure_ascii=False), encoding="utf-8")
    try:
        progress(f"Formatted source text into {len(sections)} page(s).")
        try:
            _run_synthesis(
                sections_file,
                image_dir,
                image_prefix,
                distro,
                hst_dir,
                line_graph_dir,
                progress,
            )
        finally:
            if terminate_wsl:
                subprocess.run(
                    ["wsl.exe", "--terminate", distro],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )

        missing = [path for path in image_paths if not path.is_file()]
        if missing:
            raise RuntimeError(f"Synthesis did not create {missing[0].name}.")
        pdf_path = pdf_dir / f"{pdf_name}.pdf"
        progress(f"Creating {pdf_path.name} from {len(image_paths)} image(s)…")
        process_stitch_export(
            image_paths,
            pdf_path,
            ExportMode.PDF,
            image_settings,
            delete_originals=True,
        )
        progress(f"Complete: {pdf_path.name}")
        return AutoPipelineResult(pdf_path=pdf_path, page_count=len(image_paths))
    finally:
        try:
            sections_file.unlink(missing_ok=True)
            work_dir.rmdir()
        except OSError:
            LOGGER.warning("Could not remove AUTO work directory %s", work_dir, exc_info=True)
