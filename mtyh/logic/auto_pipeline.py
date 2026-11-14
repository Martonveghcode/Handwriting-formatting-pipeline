from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
import uuid
from collections.abc import Callable, Iterable
from contextlib import ExitStack
from dataclasses import dataclass
from functools import wraps
from pathlib import Path

from mtyh.logic.config_store import default_auto_config, file_lock
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


class AutoBusyError(RuntimeError):
    pass


def exclusive_auto(function):
    """Coordinate WSL synthesis and example loading across MTYH processes."""
    @wraps(function)
    def run(*args, **kwargs):
        with ExitStack() as stack:
            try:
                stack.enter_context(file_lock(app_data_dir() / "auto.lock", blocking=False))
            except OSError as exc:
                raise AutoBusyError("Another MTYH process is using AUTO or reading glyph coverage.") from exc
            return function(*args, **kwargs)
    return run


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
    _, sections, _ = format_auto_source(source_text, formatter_settings, character_pairs, replacement_direction)
    return sections


def format_auto_source(
    source_text: str,
    formatter_settings: FormatterSettings,
    character_pairs: Iterable[tuple[str, str]],
    replacement_direction: str,
) -> tuple[str, list[str], bool]:
    mapping = build_replacement_mapping(character_pairs, replacement_direction)
    processed = apply_replacements(source_text, mapping)
    formatted, oversized = format_text(processed, formatter_settings)
    sections = split_formatted_sections(formatted)
    if not sections:
        raise ValueError("The source text did not produce any sections.")
    return formatted, sections, oversized


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


def _quote_wsl_path(path: str) -> str:
    if path.startswith("~/"):
        return '"$HOME"/' + shlex.quote(path[2:])
    return shlex.quote(path)


def _hst_command(distro: str, hst_dir: str, line_graph_dir: str, *args: str, display: bool) -> list[str]:
    script = resource_path("synthesis", "hst_batch.py")
    if not script.is_file():
        raise FileNotFoundError(f"The AUTO synthesis adapter is missing: {script}")

    python = shlex.join([
        "python3",
        windows_to_wsl_path(script),
        *args,
    ])
    python += f" --line-graphs={_quote_wsl_path(line_graph_dir)}"
    launch = python
    if display:
        launch = f"if command -v xvfb-run >/dev/null; then GDK_BACKEND=x11 xvfb-run -a {python}; else {python}; fi"
    command = f"cd -- {_quote_wsl_path(hst_dir)} && export OMP_NUM_THREADS=8 OMP_DYNAMIC=FALSE && {launch}"
    return ["wsl.exe", "--distribution", distro, "--", "bash", "-lc", command]


@exclusive_auto
def read_glyph_coverage(auto_config: dict[str, object]) -> dict:
    command = _hst_command(
        str(auto_config["wsl_distro"]),
        str(auto_config["hst_dir"]),
        str(auto_config["line_graph_dir"]),
        "--glyphs-only",
        display=False,
    )
    run = subprocess.run(
        command,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    for line in reversed(run.stdout.splitlines()):
        if line.startswith("MTYH_PROGRESS "):
            report = json.loads(line.removeprefix("MTYH_PROGRESS "))
            if report.get("stage") == "glyphs" and run.returncode == 0:
                return report
            if report.get("stage") == "error":
                raise RuntimeError(str(report["message"]))
    details = "\n".join((run.stderr or run.stdout).replace("\x00", "").splitlines()[-6:])
    raise RuntimeError(f"Could not read HST glyphs. {details}".strip())


def _run_synthesis(
    sections_file: Path,
    image_dir: Path,
    image_prefix: str,
    distro: str,
    hst_dir: str,
    line_graph_dir: str,
    progress_callback: ProgressCallback,
    *,
    strict_characters: bool = False,
) -> None:
    command = _hst_command(
        distro,
        hst_dir,
        line_graph_dir,
        f"--sections-json={windows_to_wsl_path(sections_file)}",
        f"--output-dir={windows_to_wsl_path(image_dir)}",
        f"--prefix={image_prefix}",
        *(["--strict-characters"] if strict_characters else []),
        display=True,
    )
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creation_flags,
    )
    recent_output: list[str] = []
    reported_error = None
    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.rstrip()
        LOGGER.info("HST: %s", line)
        if line.startswith("MTYH_PROGRESS "):
            try:
                payload = json.loads(line.removeprefix("MTYH_PROGRESS "))
            except json.JSONDecodeError:
                continue
            if payload.get("stage") == "error":
                reported_error = payload.get("message")
            message = _progress_message(payload)
            if message:
                progress_callback(message)
        elif line:
            recent_output.append(line)
            del recent_output[:-12]
    return_code = process.wait()
    if return_code:
        details = str(reported_error or "\n".join(recent_output[-6:]))
        suffix = f"\n\n{details}" if details else ""
        raise RuntimeError(f"Handwriting synthesis exited with code {return_code}.{suffix}")


def pdf_stem(pdf_name: str) -> str:
    name = validate_file_component(pdf_name, "final PDF name")
    if name.casefold().endswith(".pdf"):
        name = validate_file_component(name[:-4], "final PDF name")
    return name


@exclusive_auto
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
    *,
    strict_characters: bool = False,
    overwrite_pdf: bool = True,
) -> AutoPipelineResult:
    progress = progress_callback or (lambda _message: None)
    if not source_text.strip():
        raise ValueError("Paste source text before running AUTO.")
    image_prefix = validate_file_component(image_prefix, "generated image prefix")
    pdf_name = pdf_stem(pdf_name)

    defaults = default_auto_config()
    options = auto_config or {}
    image_dir = Path(str(options.get("image_output_dir") or defaults["image_output_dir"]))
    pdf_dir = Path(str(options.get("pdf_output_dir") or defaults["pdf_output_dir"]))
    distro = str(options.get("wsl_distro") or defaults["wsl_distro"])
    hst_dir = str(options.get("hst_dir") or defaults["hst_dir"])
    line_graph_dir = str(options.get("line_graph_dir") or defaults["line_graph_dir"])
    terminate_wsl = bool(options.get("terminate_wsl", True))
    pdf_path = pdf_dir / f"{pdf_name}.pdf"
    if not overwrite_pdf and pdf_path.exists():
        raise FileExistsError(f"The PDF already exists: {pdf_path}. Choose a different PDF name.")

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
    try:
        sections_file.write_text(json.dumps(sections, ensure_ascii=False), encoding="utf-8")
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
                **({"strict_characters": True} if strict_characters else {}),
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
        progress(f"Creating {pdf_path.name} from {len(image_paths)} image(s)…")
        process_stitch_export(
            image_paths,
            pdf_path,
            ExportMode.PDF,
            image_settings,
            delete_originals=True,
            overwrite=overwrite_pdf,
        )
        progress(f"Complete: {pdf_path.name}")
        return AutoPipelineResult(pdf_path=pdf_path, page_count=len(image_paths))
    finally:
        try:
            sections_file.unlink(missing_ok=True)
            work_dir.rmdir()
        except OSError:
            LOGGER.warning("Could not remove AUTO work directory %s", work_dir, exc_info=True)
