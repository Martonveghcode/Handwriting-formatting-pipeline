from mtyh.logic.text_formatter import (
    PAGE_BREAK_LINE,
    FormatterSettings,
    apply_replacements,
    build_replacement_mapping,
    format_text,
    normalize_character_map,
    split_formatted_sections,
    split_into_paragraphs,
)


def test_split_into_paragraphs_joins_wrapped_lines() -> None:
    source = "First line\ncontinues here\n\nSecond paragraph"
    assert split_into_paragraphs(source) == ["First line continues here", "Second paragraph"]


def test_apply_default_style_replacements() -> None:
    mapping = build_replacement_mapping({"\u00e1": "#", "\u00e9": "$"})
    assert apply_replacements("\u00e1 \u00e9", mapping) == "# $"


def test_format_text_adds_page_breaks() -> None:
    settings = FormatterSettings(
        min_words=2,
        max_words=2,
        target_width=20,
        tolerance=20,
        lines_per_page=2,
    )
    output, oversized = format_text("one two three four five six", settings)
    assert oversized
    assert PAGE_BREAK_LINE in output
    assert output.endswith("<            <")


def test_format_text_reports_oversized_paragraph() -> None:
    settings = FormatterSettings(
        min_words=1,
        max_words=1,
        target_width=20,
        tolerance=20,
        lines_per_page=1,
    )
    _, oversized = format_text("one two", settings)
    assert oversized


def test_character_map_defaults_are_grouped_alphabetically() -> None:
    pairs = normalize_character_map()
    assert [source for source, _ in pairs[:12]] == ["á", "à", "ç", "é", "è", "í", "ï", "ñ", "ó", "ò", "ú", "ü"]


def test_character_map_accepts_editable_pair_records() -> None:
    pairs = normalize_character_map([{"source": "á", "result": "#"}, {"source": "é", "result": "$"}])
    assert pairs == [("á", "#"), ("é", "$")]


def test_split_formatted_sections_uses_existing_page_separator() -> None:
    output = "line 1\nline 2\n---------------\nline 3"
    assert split_formatted_sections(output) == ["line 1\nline 2", "line 3"]


def test_copy_sections_do_not_create_a_spacer_only_final_section() -> None:
    output = "line 1\n<                <\n---------------\n<            <"
    assert split_formatted_sections(output) == ["line 1\n<                <\n<            <"]


def test_quick_copy_queue_handles_33_line_pages_and_short_final_page() -> None:
    settings = FormatterSettings(min_words=1, max_words=1, target_width=20, tolerance=20, lines_per_page=33)
    output, _ = format_text(" ".join(f"word{index}" for index in range(70)), settings)
    sections = split_formatted_sections(output)
    assert len(sections) == 3
    assert sum(line.startswith("< word") for line in sections[0].splitlines()) == 33
    assert sum(line.startswith("< word") for line in sections[1].splitlines()) == 33
    assert sum(line.startswith("< word") for line in sections[2].splitlines()) == 4
