#!/usr/bin/env python3
"""Hidden batch adapter for the existing HST GUI implementation.

This intentionally calls HST's existing generation method instead of copying or
changing the delicate handwriting synthesis algorithm.  The Gtk window is never
shown and no Gtk event loop is started.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path


PROGRESS_PREFIX = "MTYH_PROGRESS "


def report(stage: str, **values: object) -> None:
    payload = {"stage": stage, **values}
    print(PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate HST images without showing its GUI")
    parser.add_argument("--sections-json", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--line-graphs", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sections_path = Path(args.sections_json)
    output_dir = Path(args.output_dir)
    graph_dir = Path(args.line_graphs)

    sections = json.loads(sections_path.read_text(encoding="utf-8"))
    if not isinstance(sections, list) or not sections or not all(isinstance(item, str) for item in sections):
        raise ValueError("The formatted section file is empty or invalid.")

    graph_paths = sorted(graph_dir.glob("*.line_graph"), key=lambda path: path.name.casefold())
    if not graph_paths:
        raise FileNotFoundError(f"No .line_graph examples were found in {graph_dir}")

    # A script launched from /mnt/c gets that script folder as sys.path[0], so add
    # the caller-selected HST working directory explicitly.
    sys.path.insert(0, os.getcwd())
    from hst import HST

    report("starting", sections=len(sections), examples=len(graph_paths))
    window = HST()
    try:
        # Examples only.  Deliberately leave window.chunk_db (the Style tab) empty.
        loaded_files = 0
        loaded_glyphs = 0
        for index, graph_path in enumerate(graph_paths, start=1):
            count = int(window.glyph_db.add(str(graph_path)))
            if count:
                loaded_files += 1
                loaded_glyphs += count
            if index == 1 or index % 10 == 0 or index == len(graph_paths):
                report(
                    "loading_examples",
                    current=index,
                    total=len(graph_paths),
                    loaded_files=loaded_files,
                    loaded_glyphs=loaded_glyphs,
                )

        if window.glyph_db.empty():
            raise RuntimeError("The example files did not provide any usable glyphs.")

        output_dir.mkdir(parents=True, exist_ok=True)
        for index, section in enumerate(sections, start=1):
            report("generating", current=index, total=len(sections))
            window.text.get_buffer().set_text(section)
            window._HST__generate(None)
            if window.image.get_original() is None:
                raise RuntimeError(f"Synthesis produced no image for section {index}.")
            output_path = output_dir / f"{args.prefix} {index}.png"
            window.image.get_original().write_to_png(str(output_path))
            report("generated", current=index, total=len(sections), path=str(output_path))

        report("complete", generated=len(sections))
        return 0
    finally:
        window.destroy()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        report("error", message=str(exc))
        traceback.print_exc(file=sys.stderr)
        raise SystemExit(1)
