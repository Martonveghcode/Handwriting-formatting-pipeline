from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

DEFAULT_REPLACEMENTS: list[tuple[str, str]] = [
    ("\u00e1", "#"),
    ("\u00e0", "~"),
    ("\u00e7", "@"),
    ("\u00e9", "$"),
    ("\u00e8", "]"),
    ("\u00ed", "["),
    ("\u00ef", "|"),
    ("\u00f1", "*"),
    ("\u00f3", "^"),
    ("\u00f2", "}"),
    ("\u00fa", "`"),
    ("\u00fc", "{"),
    ('"', '"'),
    ("(", "("),
    (")", ")"),
    ("!", "!"),
    ("%", "%"),
    ("?", "?"),
    ("-", "-"),
    (":", ":"),
    (";", ";"),
    ("/", "/"),
    ("'", "'"),
]

PARAGRAPH_SPACER = "<            <"
PAGE_BREAK_LINE = "---------------"
PAGE_END_MARKER = "<                <"

REPLACEMENT_RESULT_TO_KEY = "result_to_key"
REPLACEMENT_KEY_TO_RESULT = "key_to_result"


@dataclass(frozen=True)
class FormatterSettings:
    min_words: int = 7
    max_words: int = 10
    target_width: int = 54
    tolerance: int = 4
    lines_per_page: int = 33


def replacement_defaults() -> list[tuple[str, str]]:
    return list(DEFAULT_REPLACEMENTS)


def normalize_character_map(
    values: Mapping[str, str] | Iterable[tuple[str, str] | Mapping[str, Any]] | None = None,
) -> list[tuple[str, str]]:
    """Return editable source/result pairs, migrating the legacy mapping format."""
    if values is None:
        return replacement_defaults()

    if isinstance(values, Mapping):
        incoming = {str(source): str(result) for source, result in values.items()}
        pairs = [
            (source, (incoming.get(source, default_result).strip() or default_result)[0])
            for source, default_result in DEFAULT_REPLACEMENTS
        ]
        known = {source for source, _ in DEFAULT_REPLACEMENTS}
        pairs.extend(
            (source.strip()[0], result.strip()[0])
            for source, result in incoming.items()
            if source.strip() and result.strip() and source not in known
        )
        return pairs

    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_pair in values:
        if isinstance(raw_pair, Mapping):
            source = str(raw_pair.get("source", "")).strip()
            result = str(raw_pair.get("result", "")).strip()
        else:
            try:
                source, result = raw_pair
            except (TypeError, ValueError):
                continue
            source = str(source).strip()
            result = str(result).strip()
        if not source or not result:
            continue
        source = source[0]
        result = result[0]
        if source in seen:
            continue
        seen.add(source)
        pairs.append((source, result))
    return pairs or replacement_defaults()


def normalize_replacements(
    values: Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
) -> dict[str, str]:
    return dict(normalize_character_map(values))


def build_replacement_mapping(
    replacements: Mapping[str, str] | Iterable[tuple[str, str] | Mapping[str, Any]],
    direction: str = REPLACEMENT_RESULT_TO_KEY,
) -> dict[str, str]:
    normalized = normalize_character_map(replacements)
    if direction == REPLACEMENT_KEY_TO_RESULT:
        return {result: source for source, result in normalized}
    return dict(normalized)


def split_formatted_sections(formatted: str) -> list[str]:
    """Split generated output at the existing page separator without copying it."""
    sections: list[str] = []
    current: list[str] = []
    for line in formatted.splitlines():
        if line == PAGE_BREAK_LINE:
            if current:
                sections.append("\n".join(current).strip())
                current = []
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current).strip())
    if len(sections) > 1 and sections[-1] == PARAGRAPH_SPACER:
        sections[-2] = f"{sections[-2]}\n{sections[-1]}"
        sections.pop()
    return [section for section in sections if section]


def apply_replacements(text: str, mapping: Mapping[str, str]) -> str:
    output = text
    for original, replacement in mapping.items():
        output = output.replace(original, replacement)
    return output


def split_into_paragraphs(text: str) -> list[str]:
    paragraphs: list[str] = []
    current_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line:
            current_lines.append(line)
        elif current_lines:
            paragraphs.append(" ".join(current_lines))
            current_lines = []
    if current_lines:
        paragraphs.append(" ".join(current_lines))

    if not paragraphs and text.strip():
        paragraphs.append(text.strip())
    return paragraphs


def format_paragraph(
    words: Sequence[str],
    min_words: int = 7,
    max_words: int = 10,
    target_width: int = 54,
    tolerance: int = 4,
) -> list[str]:
    min_words = max(1, int(min_words))
    max_words = max(min_words, int(max_words))
    target_width = max(1, int(target_width))
    tolerance = max(0, int(tolerance))

    lines: list[str] = []
    index = 0
    total_words = len(words)

    while index < total_words:
        best_line: Sequence[str] | None = None
        best_diff = float("inf")

        for count in range(max_words, min_words - 1, -1):
            if index + count > total_words:
                continue
            segment = words[index : index + count]
            length = len(" ".join(segment))
            diff = abs(length - target_width)
            if length <= target_width + tolerance and diff < best_diff:
                best_line = segment
                best_diff = diff

        if best_line:
            lines.append(f"< {' '.join(best_line)} <")
            index += len(best_line)
            continue

        temp_line: list[str] = []
        total_len = 0
        while index < total_words and len(temp_line) < max_words:
            next_len = len(words[index]) + (1 if temp_line else 0)
            if total_len + next_len > target_width + tolerance:
                break
            total_len += next_len
            temp_line.append(words[index])
            index += 1

        if temp_line:
            lines.append(f"< {' '.join(temp_line)} <")
        else:
            lines.append(f"< {words[index]} <")
            index += 1

    return lines


def format_text(
    input_text: str,
    settings: FormatterSettings | None = None,
) -> tuple[str, bool]:
    settings = settings or FormatterSettings()
    paragraphs = split_into_paragraphs(input_text)
    if not paragraphs:
        return PARAGRAPH_SPACER, False

    formatted_lines: list[str] = []
    page_line_count = 0
    effective_limit = max(0, int(settings.lines_per_page))
    oversized_paragraph = False

    for paragraph_index, paragraph_text in enumerate(paragraphs):
        words = paragraph_text.split()
        if not words:
            continue

        paragraph_lines = format_paragraph(
            words=words,
            min_words=settings.min_words,
            max_words=settings.max_words,
            target_width=settings.target_width,
            tolerance=settings.tolerance,
        )

        if effective_limit and len(paragraph_lines) > effective_limit:
            oversized_paragraph = True

        for line in paragraph_lines:
            if effective_limit and page_line_count == effective_limit:
                formatted_lines.append(PAGE_END_MARKER)
                formatted_lines.append(PAGE_BREAK_LINE)
                page_line_count = 0
            formatted_lines.append(line)
            if effective_limit:
                page_line_count += 1

        if paragraph_index < len(paragraphs) - 1:
            if effective_limit and page_line_count == effective_limit:
                formatted_lines.append(PAGE_END_MARKER)
                formatted_lines.append(PAGE_BREAK_LINE)
                page_line_count = 0
            else:
                formatted_lines.append(PARAGRAPH_SPACER)
                if effective_limit:
                    page_line_count += 1
                    if page_line_count == effective_limit:
                        formatted_lines.append(PAGE_END_MARKER)
                        formatted_lines.append(PAGE_BREAK_LINE)
                        page_line_count = 0

    if not formatted_lines or formatted_lines[-1] != PARAGRAPH_SPACER:
        if effective_limit and page_line_count == effective_limit:
            formatted_lines.append(PAGE_END_MARKER)
            formatted_lines.append(PAGE_BREAK_LINE)
        formatted_lines.append(PARAGRAPH_SPACER)

    return "\n".join(formatted_lines), oversized_paragraph
