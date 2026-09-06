from pathlib import Path

import pytest
from PIL import Image

from mtyh.logic import auto_pipeline
from mtyh.logic.image_tools import ImageProcessingSettings
from mtyh.logic.text_formatter import FormatterSettings, REPLACEMENT_RESULT_TO_KEY


def test_prepare_sections_uses_formatter_replacements() -> None:
    sections = auto_pipeline.prepare_sections(
        "área pequeña",
        FormatterSettings(min_words=1, max_words=10, target_width=54, lines_per_page=33),
        [("á", "#"), ("ñ", "*")],
        REPLACEMENT_RESULT_TO_KEY,
    )
    assert "#rea peque*a" in sections[0]


@pytest.mark.parametrize("value", ["", "bad/name", "CON", "trailing."])
def test_validate_file_component_rejects_invalid_names(value: str) -> None:
    with pytest.raises(ValueError):
        auto_pipeline.validate_file_component(value, "name")


def test_auto_pipeline_generates_pdf_and_cleans_images(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_dir = tmp_path / "images"
    pdf_dir = tmp_path / "pdfs"
    messages: list[str] = []

    def fake_synthesis(
        sections_file: Path,
        target_dir: Path,
        prefix: str,
        _distro: str,
        _hst_dir: str,
        _line_graph_dir: str,
        progress_callback,
    ) -> None:
        sections = __import__("json").loads(sections_file.read_text(encoding="utf-8"))
        target_dir.mkdir(parents=True, exist_ok=True)
        for index, _section in enumerate(sections, start=1):
            Image.new("RGB", (120, 80), "white").save(target_dir / f"{prefix} {index}.png")
        progress_callback("Generated test pages.")

    monkeypatch.setattr(auto_pipeline, "_run_synthesis", fake_synthesis)
    result = auto_pipeline.run_auto_pipeline(
        "one two three four five six seven eight nine ten",
        "Spanish",
        "Spanish Homework.pdf",
        FormatterSettings(min_words=1, max_words=2, target_width=12, lines_per_page=2),
        [],
        REPLACEMENT_RESULT_TO_KEY,
        ImageProcessingSettings(connect_lines=False, remove_yellow=False, dpi=72),
        {
            "image_output_dir": str(image_dir),
            "pdf_output_dir": str(pdf_dir),
            "terminate_wsl": False,
        },
        progress_callback=messages.append,
    )

    assert result.page_count > 1
    assert result.pdf_path == pdf_dir / "Spanish Homework.pdf"
    assert result.pdf_path.read_bytes().startswith(b"%PDF")
    assert not list(image_dir.glob("Spanish *.png"))
    assert any("Formatted" in message for message in messages)
