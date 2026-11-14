"""Loopback HTTP API for the desktop app's AUTO workflow."""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import secrets
import shutil
import string
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Condition, Thread
from urllib.parse import parse_qs, urlsplit

from mtyh.logic.api_settings import (
    apply_settings_patch,
    formatter_inputs,
    output_directory,
    public_settings,
    stitch_settings,
)
from mtyh.logic.auto_pipeline import (
    AutoBusyError,
    format_auto_source,
    pdf_stem,
    read_glyph_coverage,
    run_auto_pipeline,
    validate_file_component,
)
from mtyh.logic.config_store import ConfigStore, file_lock
from mtyh.logic.text_formatter import (
    PAGE_BREAK_LINE,
    PAGE_END_MARKER,
    PARAGRAPH_SPACER,
    apply_replacements,
    build_replacement_mapping,
)
from mtyh.logging_config import configure_logging
from mtyh.paths import app_data_dir

LOGGER = logging.getLogger(__name__)
MAX_BODY_BYTES = 2 * 1024 * 1024
JOB_HISTORY = 100
DRAFT_FIELDS = {"source_text", "image_prefix", "pdf_name", "image_output_dir", "pdf_output_dir"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def pdf_identity(info: os.stat_result) -> tuple[int, int, int]:
    return info.st_ino, info.st_mtime_ns, info.st_size


@dataclass
class Job:
    id: str
    created_at: str
    status: str = "running"
    progress: str = "Starting AUTO workflow."
    finished_at: str | None = None
    error: dict | None = None
    result: dict | None = None

    def snapshot(self) -> dict:
        return asdict(self)


def validate_draft(data: dict) -> dict[str, str]:
    clean = {}
    for key, value in data.items():
        if key not in DRAFT_FIELDS:
            raise ValueError(f"Unknown AUTO field {key}.")
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError(f"{key} must be a string without NUL characters.")
        value.encode("utf-8")
        if key.endswith("_dir"):
            value = output_directory(value, key)
        elif key == "image_prefix" and value:
            value = validate_file_component(value, "generated image prefix")
        elif key == "pdf_name" and value:
            pdf_stem(value)
            value = value.strip()
        clean[key] = value
    return clean


class AutoApi:
    def __init__(self, config: ConfigStore, show_draft: Callable[[dict], None] | None = None) -> None:
        self.config = config
        self.show_draft = show_draft
        self.changed = Condition()
        self.draft: dict[str, str] = {}
        self.jobs: dict[str, Job] = {}
        self.pdf_identities: dict[str, tuple[int, int, int]] = {}
        self.active: Job | None = None
        self.glyph_scan_running = False

    def settings(self, patch: dict | None = None) -> dict:
        with self.changed:
            if patch is not None:
                config = self.config.update(lambda current: apply_settings_patch(current, patch))
            else:
                config = self.config.load()
            return public_settings(config)

    def _draft_snapshot(self, settings: dict) -> dict[str, str]:
        return {
            **{key: settings["auto"][key] for key in DRAFT_FIELDS - {"source_text"}},
            "source_text": "",
            **self.draft,
        }

    def edit_draft(self, patch: dict | None = None) -> dict:
        with self.changed:
            if patch is not None:
                self.draft.update(validate_draft(patch))
            draft = self._draft_snapshot(self.settings())
            if patch is not None and self.show_draft is not None:
                self.show_draft(draft)
            return draft

    def _coverage(self, settings: dict) -> dict:
        with self.changed:
            if self.active is not None or self.glyph_scan_running:
                raise AutoBusyError("AUTO is busy. Try again when the current operation finishes.")
            self.glyph_scan_running = True
        try:
            result = read_glyph_coverage(settings["auto"])
        except (AutoBusyError, ValueError):
            raise
        except Exception as exc:
            LOGGER.exception("Could not read handwriting coverage")
            raise ApiError(503, "glyphs_unavailable", str(exc)) from exc
        finally:
            with self.changed:
                self.glyph_scan_running = False
        result.pop("stage")
        return result

    def text_format(self) -> dict:
        settings = self.settings()
        _, pairs, direction = formatter_inputs(settings)
        mapping = build_replacement_mapping(pairs, direction)
        coverage = self._coverage(settings)
        glyphs = set(coverage["glyph_characters"] + coverage["case_fallback_characters"])
        accepted = {
            char for char in glyphs | mapping.keys()
            if set(apply_replacements(char, mapping)) <= glyphs
        }
        return {
            **coverage,
            "accepted_source_characters": sorted(accepted),
            "formatter": settings["formatter"],
            "effective_replacements": mapping,
            "replacement_order": list(mapping),
            "source_format": {
                "encoding": "UTF-8 JSON string",
                "paragraphs": "Separate paragraphs with blank lines. Single newlines within a paragraph are reflowed.",
                "whitespace": "The formatter handles spaces, tabs and line breaks.",
                "line_template": "< {words} <",
                "paragraph_spacer": PARAGRAPH_SPACER,
                "page_end_marker": PAGE_END_MARKER,
                "page_separator": PAGE_BREAK_LINE,
            },
        }

    def preview(self, data: dict) -> dict:
        if data.keys() - {"source_text", "check_characters"}:
            raise ValueError("Format accepts source_text and check_characters.")
        check = data.get("check_characters", False)
        if type(check) is not bool:
            raise ValueError("check_characters must be a boolean.")
        with self.changed:
            settings = self.settings()
            source = data.get("source_text", self._draft_snapshot(settings)["source_text"])
        source = validate_draft({"source_text": source})["source_text"]
        if not source.strip():
            raise ValueError("Enter source_text before formatting.")
        formatted, sections, oversized = format_auto_source(source, *formatter_inputs(settings))
        missing = None
        if check:
            coverage = self._coverage(settings)
            supported = set(coverage["glyph_characters"] + coverage["case_fallback_characters"] + list(string.whitespace))
            missing = sorted(set("".join(sections)) - supported)
        return {
            "formatted_text": formatted,
            "sections": sections,
            "section_count": len(sections),
            "oversized_paragraph": oversized,
            "unsupported_characters": missing,
        }

    def start_job(self, data: dict) -> dict:
        fields = data.copy()
        overwrite = fields.pop("overwrite_pdf", False)
        if type(overwrite) is not bool:
            raise ValueError("overwrite_pdf must be a boolean.")
        with self.changed:
            if self.active is not None or self.glyph_scan_running:
                raise AutoBusyError("AUTO is busy. Try again when the current operation finishes.")
            settings = self.settings()
            draft = {**self._draft_snapshot(settings), **validate_draft(fields)}
            if not draft["source_text"].strip():
                raise ValueError("Enter source_text before starting AUTO.")
            prefix = validate_file_component(draft["image_prefix"], "generated image prefix")
            name = draft["pdf_name"]
            stem = pdf_stem(name)
            options = dict(settings["auto"])
            for key in ("image_output_dir", "pdf_output_dir"):
                options[key] = output_directory(draft[key], key)
            pdf = Path(options["pdf_output_dir"]) / f"{stem}.pdf"
            if pdf.exists() and not overwrite:
                raise ApiError(409, "output_exists", f"The PDF already exists: {pdf}.")
            job = Job(uuid.uuid4().hex, timestamp())
            args = (draft["source_text"], prefix, name, *formatter_inputs(settings), stitch_settings(settings), options)
            self.jobs[job.id] = job
            self.active = job
            try:
                Thread(
                    target=self._run_job,
                    args=(job, args, overwrite),
                    name="MTYH AUTO",
                    daemon=False,
                ).start()
            except RuntimeError:
                del self.jobs[job.id]
                self.active = None
                raise
            while len(self.jobs) > JOB_HISTORY:
                oldest = next(iter(self.jobs))
                del self.jobs[oldest]
                self.pdf_identities.pop(oldest, None)
            if self.show_draft is not None:
                self.show_draft(draft)
            return job.snapshot()

    def _run_job(self, job: Job, args: tuple, overwrite: bool) -> None:
        def progress(message: str) -> None:
            with self.changed:
                job.progress = message

        error = None
        result = None
        identity = None
        try:
            exported = run_auto_pipeline(
                *args,
                progress_callback=progress,
                strict_characters=True,
                overwrite_pdf=overwrite,
            )
            result = {
                "pdf_path": str(exported.pdf_path),
                "section_count": exported.page_count,
                "download_path": f"/v1/jobs/{job.id}/pdf",
            }
            identity = pdf_identity(exported.pdf_path.stat())
        except Exception as exc:
            LOGGER.exception("AUTO job %s failed", job.id)
            error = {"code": type(exc).__name__, "message": str(exc)}
        finally:
            with self.changed:
                if identity is None:
                    job.error = error
                    job.status = "failed"
                    job.progress = "AUTO failed."
                else:
                    self.pdf_identities[job.id] = identity
                    job.result = result
                    job.status = "succeeded"
                job.finished_at = timestamp()
                self.active = None
                self.changed.notify_all()

    def get_job(self, job_id: str, wait: int = 0) -> dict:
        with self.changed:
            job = self.jobs.get(job_id)
            if job is None:
                raise ApiError(404, "job_not_found", "This job is not in the current server's history.")
            if wait:
                self.changed.wait_for(lambda: job.finished_at is not None, timeout=wait)
            return job.snapshot()

    def list_jobs(self) -> dict:
        with self.changed:
            return {"jobs": [job.snapshot() for job in reversed(self.jobs.values())]}

    def job_pdf(self, job_id: str) -> tuple[Path, tuple[int, int, int]]:
        with self.changed:
            job = self.get_job(job_id)
            if job["status"] != "succeeded":
                raise ApiError(409, "pdf_unavailable", "The job must succeed before its PDF can be downloaded.")
            return Path(job["result"]["pdf_path"]), self.pdf_identities[job_id]


class LocalServer(ThreadingHTTPServer):
    def __init__(self, port: int, api: AutoApi, token: str) -> None:
        self.api = api
        self.authorization = f"Bearer {token}".encode("ascii")
        super().__init__(("127.0.0.1", port), RequestHandler)


class RequestHandler(BaseHTTPRequestHandler):
    server: LocalServer
    timeout = 45

    def _json(self, status: int, value: dict) -> None:
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > MAX_BODY_BYTES:
            raise ApiError(413, "invalid_size", f"JSON bodies can be up to {MAX_BODY_BYTES} bytes.")
        if not length:
            return {}
        if self.headers.get_content_type() != "application/json":
            raise ApiError(415, "content_type", "Send JSON with Content-Type: application/json.")
        value = json.loads(self.rfile.read(length).decode("utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("The JSON body must be an object.")
        return value

    def _download(self, job_id: str) -> None:
        path, identity = self.server.api.job_pdf(job_id)
        try:
            stream = path.open("rb")
        except FileNotFoundError as exc:
            raise ApiError(404, "pdf_not_found", "The generated PDF has been moved or removed.") from exc
        with stream:
            info = os.fstat(stream.fileno())
            if pdf_identity(info) != identity:
                raise ApiError(410, "pdf_replaced", "The generated PDF has changed since this job finished.")
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(info.st_size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            shutil.copyfileobj(stream, self.wfile)

    def _dispatch(self) -> None:
        try:
            url = urlsplit(self.path)
            route = (self.command, url.path)
            if route == ("GET", "/v1/health"):
                self._json(200, {"service": "MTYH", "api_version": 1, "status": "ready"})
                return
            auth = self.headers.get("Authorization", "").encode("latin-1")
            if not hmac.compare_digest(self.server.authorization, auth):
                raise ApiError(401, "unauthorized", "Supply the local API bearer token.")
            api = self.server.api
            if route == ("GET", "/v1/settings"):
                self._json(200, api.settings())
            elif route == ("PATCH", "/v1/settings"):
                self._json(200, api.settings(self._body()))
            elif route == ("GET", "/v1/auto"):
                self._json(200, api.edit_draft())
            elif route == ("PATCH", "/v1/auto"):
                self._json(200, api.edit_draft(self._body()))
            elif route == ("GET", "/v1/text-format"):
                self._json(200, api.text_format())
            elif route == ("POST", "/v1/format"):
                self._json(200, api.preview(self._body()))
            elif route == ("POST", "/v1/jobs"):
                self._json(202, api.start_job(self._body()))
            elif route == ("GET", "/v1/jobs"):
                self._json(200, api.list_jobs())
            elif self.command == "GET" and url.path.startswith("/v1/jobs/"):
                parts = url.path.split("/")
                if len(parts) == 5 and parts[4] == "pdf":
                    self._download(parts[3])
                elif len(parts) == 4 and parts[3]:
                    wait = int(parse_qs(url.query).get("wait", ["0"])[0])
                    if not 0 <= wait <= 30:
                        raise ValueError("wait must be an integer between 0 and 30 seconds.")
                    self._json(200, api.get_job(parts[3], wait))
                else:
                    raise ApiError(404, "route_not_found", "Unknown API route.")
            else:
                raise ApiError(404, "route_not_found", "Unknown API route.")
        except ApiError as exc:
            self._json(exc.status, {"error": {"code": exc.code, "message": str(exc)}})
        except (ValueError, RecursionError) as exc:
            self._json(400, {"error": {"code": "invalid_request", "message": str(exc)}})
        except AutoBusyError as exc:
            self._json(409, {"error": {"code": "busy", "message": str(exc)}})
        except (BrokenPipeError, ConnectionError, TimeoutError):
            return
        except Exception:
            LOGGER.exception("API request failed")
            self._json(500, {"error": {"code": "internal_error", "message": "See the MTYH log for details."}})

    do_GET = do_POST = do_PATCH = _dispatch

    def log_message(self, format: str, *args) -> None:
        LOGGER.info("API: " + format, *args)


def local_token() -> str:
    path = app_data_dir() / "api-token"
    with file_lock(path.with_suffix(".lock")):
        if not path.exists():
            path.write_text(secrets.token_urlsafe(32) + "\n", encoding="ascii")
        token = path.read_text(encoding="ascii").strip()
    if len(token) < 32 or not all(char in string.ascii_letters + string.digits + "-_" for char in token):
        raise ValueError(f"Invalid MTYH API token in {path}. Remove the file to regenerate it.")
    return token


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765, help="Loopback port (default: 8765).")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be from 1 to 65535.")
    configure_logging()
    try:
        with LocalServer(args.port, AutoApi(ConfigStore()), local_token()) as server:
            print(f"MTYH API listening on http://127.0.0.1:{args.port}", flush=True)
            print(f"Bearer token file: {app_data_dir() / 'api-token'}", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        print("MTYH API stopped.", flush=True)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Could not start the MTYH API: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
