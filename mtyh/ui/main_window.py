from __future__ import annotations

import logging
import os
import re
import json
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from PySide6.QtCore import QRect, QSize, Qt, QThread, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from mtyh.logic.config_store import ConfigStore
from mtyh.logic.config_store import default_config
from mtyh.logic.image_tools import (
    ExportMode,
    ImageProcessingSettings,
    process_single_image,
    process_stitch_export,
)
from mtyh.logic.macro import MacroSettings, TrainingExportMacro
from mtyh.logic.text_formatter import (
    FormatterSettings,
    PAGE_END_MARKER,
    PARAGRAPH_SPACER,
    REPLACEMENT_KEY_TO_RESULT,
    REPLACEMENT_RESULT_TO_KEY,
    apply_replacements,
    build_replacement_mapping,
    format_text,
    normalize_character_map,
    normalize_replacements,
    replacement_defaults,
    split_formatted_sections,
)
from mtyh.paths import default_output_dir, icon_path, logs_dir
from mtyh.workers import FunctionWorker

LOGGER = logging.getLogger(__name__)

TaskRunner = Callable[[Callable[..., Any], Callable[[Any], None], Callable[[str], None], Any], None]

PALETTE_PRESETS: dict[str, tuple[str, str, str, str]] = {
    "Midnight Slate": ("#0c1821", "#1d2d44", "#f0ebd8", "#ccc9dc"),
    "Nord Blue": ("#2e3440", "#3b4252", "#eceff4", "#88c0d0"),
    "Ocean Blue": ("#071e3d", "#123b66", "#f2f8ff", "#2f9df4"),
    "Cobalt Blue": ("#101b3f", "#1d3268", "#f5f8ff", "#4f7cff"),
    "Sky Blue": ("#eaf6ff", "#ffffff", "#15324b", "#2389da"),
    "Deep Forest": ("#0b1d13", "#163b24", "#f0f7f2", "#67b26f"),
    "Emerald Green": ("#08261f", "#123d32", "#effcf7", "#34c38f"),
    "Sage Green": ("#edf3ec", "#ffffff", "#25382b", "#6f9774"),
    "Warm Sand": ("#2b2118", "#4a3728", "#fff4df", "#e39b53"),
    "Sunset Orange": ("#32170f", "#54271c", "#fff6ed", "#f47b3a"),
    "Rose Red": ("#32131b", "#55212d", "#fff2f5", "#e05270"),
    "Terracotta": ("#f8eee8", "#fffaf7", "#3d2821", "#bf684d"),
    "Lavender Night": ("#1e1833", "#332a55", "#f5f1ff", "#b89cff"),
    "Royal Purple": ("#211438", "#39245c", "#faf5ff", "#9b6bea"),
    "Soft Violet": ("#f5f0ff", "#ffffff", "#302641", "#8661c5"),
    "Clean Blue": ("#f3f7ff", "#ffffff", "#172033", "#315efb"),
    "Graphite": ("#15171a", "#25282d", "#f4f5f7", "#9aa3af"),
    "Paper": ("#f2f0ea", "#fffefa", "#292722", "#626b73"),
}
PALETTE_FAMILIES: dict[str, tuple[str, ...]] = {
    "All colours": tuple(PALETTE_PRESETS),
    "Blue": ("Nord Blue", "Ocean Blue", "Cobalt Blue", "Sky Blue", "Clean Blue"),
    "Green": ("Deep Forest", "Emerald Green", "Sage Green"),
    "Purple": ("Lavender Night", "Royal Purple", "Soft Violet"),
    "Warm": ("Warm Sand", "Sunset Orange", "Rose Red", "Terracotta"),
    "Neutral": ("Midnight Slate", "Graphite", "Paper"),
}
ONLINE_PALETTE_URL = "https://colorui.io/api/v1/palette?count=5"


def parse_palette_text(text: str) -> list[str]:
    """Extract six-digit colours from palette text or generator URLs."""
    return [
        f"#{match.lower()}"
        for match in re.findall(r"(?i)(?<![0-9a-f])#?([0-9a-f]{6})(?![0-9a-f])", text)
    ]


def color_luminance(value: str) -> float:
    color = QColor(value)
    channels = (color.redF(), color.greenF(), color.blueF())
    linear = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels]
    return (0.2126 * linear[0]) + (0.7152 * linear[1]) + (0.0722 * linear[2])


def color_contrast_ratio(foreground: str, background: str) -> float:
    light = color_luminance(foreground)
    dark = color_luminance(background)
    return (max(light, dark) + 0.05) / (min(light, dark) + 0.05)


def readable_text_color(preferred: str, background: str, minimum_ratio: float = 4.5) -> str:
    if QColor(preferred).isValid() and color_contrast_ratio(preferred, background) >= minimum_ratio:
        return QColor(preferred).name()
    candidates = ("#ffffff", "#000000")
    return max(candidates, key=lambda candidate: color_contrast_ratio(candidate, background))


def arrange_palette_for_ui(colors: Sequence[str]) -> tuple[str, str, str, str]:
    normalized = [QColor(color).name() for color in colors if QColor(color).isValid()]
    if len(normalized) < 4:
        raise ValueError("The online palette did not contain at least four valid colours.")
    ordered = sorted(dict.fromkeys(normalized), key=color_luminance)
    if len(ordered) < 4:
        raise ValueError("The online palette did not contain four distinct colours.")
    background = ordered[0]
    panel = ordered[1]
    text = readable_text_color(ordered[-1], panel)
    accent_candidates = ordered[2:-1] or ordered[2:3]
    accent = max(accent_candidates, key=lambda value: QColor(value).hsvSaturationF())
    return background, panel, text, accent


def fetch_random_online_palette(url: str = ONLINE_PALETTE_URL) -> list[str]:
    request = urllib.request.Request(url, headers={"User-Agent": "MTYH/2.0 palette client"})
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"Palette service returned HTTP {response.status}.")
        payload = json.loads(response.read().decode("utf-8"))
    raw_colors = payload.get("colors") if isinstance(payload, dict) else None
    if not isinstance(raw_colors, list):
        raise ValueError("Palette service returned an unexpected response.")
    colors = [QColor(str(color)).name() for color in raw_colors if QColor(str(color)).isValid()]
    if len(colors) < 4:
        raise ValueError("Palette service returned too few valid colours.")
    return colors


def primary_button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setProperty("role", "primary")
    button.setMinimumHeight(32)
    return button


def secondary_button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setMinimumHeight(30)
    return button


def group_box(title: str) -> QGroupBox:
    box = QGroupBox(title)
    box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
    return box


def numeric_spin(minimum: int, maximum: int, value: int = 0) -> QSpinBox:
    field = QSpinBox()
    field.setRange(minimum, maximum)
    field.setValue(value)
    field.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    field.setAlignment(Qt.AlignmentFlag.AlignLeft)
    field.setMinimumWidth(76)
    field.setMaximumWidth(110)
    field.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return field


def ensure_ext(path: Path, suffix: str) -> Path:
    if path.suffix.lower() != suffix.lower():
        return path.with_suffix(suffix)
    return path


def configured_import_dir(config: dict[str, Any]) -> Path:
    configured = Path(str(config.get("image", {}).get("import_dir", Path.home()))).expanduser()
    return configured if configured.is_dir() else Path.home()


def color_to_text(color: tuple[int, int, int]) -> str:
    return f"{color[0]},{color[1]},{color[2]}"


def parse_color_text(text: str) -> tuple[int, int, int]:
    parts = [int(part.strip()) for part in text.split(",")]
    if len(parts) != 3:
        raise ValueError("Color must use R,G,B format.")
    return tuple(max(0, min(255, value)) for value in parts)  # type: ignore[return-value]


class TextFormatterPage(QWidget):
    def __init__(
        self,
        config: dict[str, Any],
        status: Callable[[str], None],
    ) -> None:
        super().__init__()
        self.config = config
        self.status = status
        self._build()
        self._load_config()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        toolbar = QHBoxLayout()
        self.format_button = primary_button("Format")
        self.copy_button = secondary_button("Copy Output")
        self.save_button = secondary_button("Save Text")
        self.clear_button = secondary_button("Clear")
        toolbar.addWidget(self.format_button)
        toolbar.addWidget(self.copy_button)
        toolbar.addWidget(self.save_button)
        toolbar.addWidget(self.clear_button)
        toolbar.addStretch(1)
        root.addLayout(toolbar)

        splitter = QSplitter(Qt.Vertical)
        root.addWidget(splitter, 1)

        top = QSplitter(Qt.Horizontal)
        splitter.addWidget(top)

        input_panel = QWidget()
        input_layout = QVBoxLayout(input_panel)
        input_layout.setContentsMargins(0, 0, 8, 0)
        input_label = QLabel("Source text")
        input_label.setProperty("class", "sectionLabel")
        self.input_text = QPlainTextEdit()
        self.input_text.setPlaceholderText("Paste or type text to format.")
        self.input_text.setMinimumHeight(220)
        input_layout.addWidget(input_label)
        input_layout.addWidget(self.input_text, 1)
        top.addWidget(input_panel)

        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setFrameShape(QFrame.NoFrame)
        settings_body = QWidget()
        settings_layout = QVBoxLayout(settings_body)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(12)
        settings_scroll.setWidget(settings_body)
        top.addWidget(settings_scroll)
        top.setStretchFactor(0, 3)
        top.setStretchFactor(1, 2)

        rules_box = group_box("Line Rules")
        rules_form = QFormLayout(rules_box)
        self.lines_per_page = QSpinBox()
        self.lines_per_page.setRange(0, 200)
        self.min_words = QSpinBox()
        self.min_words.setRange(1, 50)
        self.max_words = QSpinBox()
        self.max_words.setRange(1, 80)
        self.target_width = QSpinBox()
        self.target_width.setRange(10, 200)
        self.tolerance = QSpinBox()
        self.tolerance.setRange(0, 50)
        rules_form.addRow("Lines per page", self.lines_per_page)
        rules_form.addRow("Min words per line", self.min_words)
        rules_form.addRow("Max words per line", self.max_words)
        rules_form.addRow("Target width", self.target_width)
        rules_form.addRow("Tolerance", self.tolerance)
        settings_layout.addWidget(rules_box)

        map_box = group_box("Character Map")
        map_layout = QVBoxLayout(map_box)
        self.direction = QComboBox()
        self.direction.addItem("Result character -> bound key", REPLACEMENT_RESULT_TO_KEY)
        self.direction.addItem("Bound key -> result character", REPLACEMENT_KEY_TO_RESULT)
        map_layout.addWidget(self.direction)
        self.replacement_table = QTableWidget(len(replacement_defaults()), 2)
        self.replacement_table.setHorizontalHeaderLabels(["Result", "Key"])
        self.replacement_table.verticalHeader().setVisible(False)
        self.replacement_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.replacement_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.replacement_table.setMinimumHeight(280)
        self.replacement_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.replacement_table.setAlternatingRowColors(True)
        map_layout.addWidget(self.replacement_table)
        settings_layout.addWidget(map_box)
        settings_layout.addStretch(1)

        output_panel = QWidget()
        output_layout = QVBoxLayout(output_panel)
        output_layout.setContentsMargins(0, 8, 0, 0)
        output_label = QLabel("Formatted output")
        output_label.setProperty("class", "sectionLabel")
        self.output_text = QPlainTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setMinimumHeight(220)
        output_layout.addWidget(output_label)
        output_layout.addWidget(self.output_text, 1)
        splitter.addWidget(output_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)

        self.format_button.clicked.connect(self.run_formatter)
        self.copy_button.clicked.connect(self.copy_output)
        self.save_button.clicked.connect(self.save_output)
        self.clear_button.clicked.connect(self.clear_text)

    def _load_config(self) -> None:
        formatter = self.config.get("formatter", {})
        self.lines_per_page.setValue(int(formatter.get("lines_per_page", 33)))
        self.min_words.setValue(int(formatter.get("min_words", 7)))
        self.max_words.setValue(int(formatter.get("max_words", 10)))
        self.target_width.setValue(int(formatter.get("target_width", 54)))
        self.tolerance.setValue(int(formatter.get("tolerance", 4)))
        direction = formatter.get("replacement_direction", REPLACEMENT_RESULT_TO_KEY)
        index = self.direction.findData(direction)
        self.direction.setCurrentIndex(max(0, index))

        replacements = normalize_replacements(formatter.get("replacements"))
        for row, (result_char, default_key) in enumerate(replacement_defaults()):
            result_item = QTableWidgetItem(result_char)
            result_item.setFlags(result_item.flags() & ~Qt.ItemIsEditable)
            key_item = QTableWidgetItem(replacements.get(result_char, default_key))
            self.replacement_table.setItem(row, 0, result_item)
            self.replacement_table.setItem(row, 1, key_item)

    def collect_config(self) -> dict[str, Any]:
        replacements = self.replacements()
        return {
            "min_words": self.min_words.value(),
            "max_words": self.max_words.value(),
            "target_width": self.target_width.value(),
            "tolerance": self.tolerance.value(),
            "lines_per_page": self.lines_per_page.value(),
            "replacement_direction": self.direction.currentData(),
            "replacements": replacements,
        }

    def replacements(self) -> dict[str, str]:
        values: dict[str, str] = {}
        for row in range(self.replacement_table.rowCount()):
            result_item = self.replacement_table.item(row, 0)
            key_item = self.replacement_table.item(row, 1)
            if not result_item:
                continue
            result_char = result_item.text()
            key_text = key_item.text() if key_item else ""
            values[result_char] = key_text.strip()[:1] or dict(replacement_defaults()).get(result_char, "")
        return normalize_replacements(values)

    def settings(self) -> FormatterSettings:
        return FormatterSettings(
            min_words=self.min_words.value(),
            max_words=max(self.min_words.value(), self.max_words.value()),
            target_width=self.target_width.value(),
            tolerance=self.tolerance.value(),
            lines_per_page=self.lines_per_page.value(),
        )

    def run_formatter(self) -> None:
        source = self.input_text.toPlainText()
        if not source.strip():
            self.status("Enter text first.")
            return
        if self.min_words.value() > self.max_words.value():
            QMessageBox.warning(self, "Check line rules", "Min words cannot be greater than max words.")
            return

        direction = self.direction.currentData()
        replacements = self.replacements()
        if direction == REPLACEMENT_KEY_TO_RESULT:
            keys = list(replacements.values())
            duplicates = sorted({key for key in keys if keys.count(key) > 1})
            if duplicates:
                QMessageBox.warning(
                    self,
                    "Duplicate keys",
                    "Duplicate bound keys were found. Later rows will win.",
                )

        mapping = build_replacement_mapping(replacements, direction)
        processed = apply_replacements(source, mapping)
        formatted, oversized = format_text(processed, self.settings())
        self.output_text.setPlainText(formatted)
        message = f"Formatted {len(formatted.splitlines())} output lines."
        if oversized:
            message += " One paragraph exceeds the page limit."
        self.status(message)
        LOGGER.info("Formatted text")

    def copy_output(self) -> None:
        content = self.output_text.toPlainText().strip()
        if not content:
            self.status("Nothing to copy.")
            return
        QApplication.clipboard().setText(content)
        self.status("Output copied.")

    def save_output(self) -> None:
        content = self.output_text.toPlainText()
        if not content.strip():
            self.status("Nothing to save.")
            return
        output_dir = Path(self.config.get("output_dir", str(default_output_dir())))
        output_dir.mkdir(parents=True, exist_ok=True)
        default_name = output_dir / f"formatted_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save formatted text",
            str(default_name),
            "Text files (*.txt);;All files (*.*)",
        )
        if not path:
            return
        Path(path).write_text(content, encoding="utf-8")
        self.status(f"Saved {Path(path).name}.")
        LOGGER.info("Saved formatted text to %s", path)

    def clear_text(self) -> None:
        if self.input_text.toPlainText().strip() or self.output_text.toPlainText().strip():
            answer = QMessageBox.question(
                self,
                "Clear text",
                "Clear source and output text?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        self.input_text.clear()
        self.output_text.clear()
        self.status("Text cleared.")


class ModernTextFormatterPage(QWidget):
    """Responsive formatter workspace with a hidden-by-default output and copy queue."""

    settings_changed = Signal()

    def __init__(self, config: dict[str, Any], status: Callable[[str], None]) -> None:
        super().__init__()
        self.config = config
        self.status = status
        self.map_pair_columns = 4
        self.map_pair_count = 0
        self.copy_sections: list[str] = []
        self.copy_index = 0
        self._loading = True
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.setInterval(220)
        self.refresh_timer.timeout.connect(lambda: self.run_formatter(silent=True))
        self._build()
        self.load_config()
        self._loading = False
        self._connect_live_updates()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        heading = QHBoxLayout()
        self.format_button = primary_button("Format Now")
        self.view_button = secondary_button("View Formatted Output")
        self.view_button.setCheckable(True)
        self.save_button = secondary_button("Save Text")
        self.clear_button = secondary_button("Clear")
        for button in (self.format_button, self.view_button, self.save_button, self.clear_button):
            heading.addWidget(button)
        heading.addStretch(1)
        root.addLayout(heading)

        queue_bar = QFrame()
        queue_bar.setObjectName("quickCopyBar")
        queue_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        queue_bar.setMinimumHeight(82)
        queue_bar.setMaximumHeight(92)
        queue_layout = QHBoxLayout(queue_bar)
        queue_layout.setContentsMargins(14, 10, 14, 10)
        queue_layout.setSpacing(12)
        self.quick_copy_button = primary_button("Quick Copy Next Section")
        self.quick_copy_button.setMinimumHeight(38)
        self.reset_queue_button = secondary_button("Reset Copy Queue")
        progress_group = QVBoxLayout()
        progress_group.setSpacing(4)
        self.queue_progress = QLabel("Format text to start the copy queue.")
        self.queue_progress.setProperty("class", "queueProgress")
        self.queue_timeline = QProgressBar()
        self.queue_timeline.setObjectName("copyTimeline")
        self.queue_timeline.setRange(0, 1)
        self.queue_timeline.setValue(0)
        self.queue_timeline.setTextVisible(False)
        self.queue_timeline.setMaximumHeight(10)
        self.queue_next = QLabel("")
        self.queue_next.setProperty("class", "muted")
        progress_group.addWidget(self.queue_progress)
        progress_group.addWidget(self.queue_timeline)
        progress_group.addWidget(self.queue_next)
        queue_layout.addLayout(progress_group, 1)
        queue_layout.addWidget(self.reset_queue_button)
        queue_layout.addWidget(self.quick_copy_button)
        root.addWidget(queue_bar)

        self.workspace_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.workspace_splitter.setChildrenCollapsible(False)

        source_box = group_box("Source Text")
        source_layout = QVBoxLayout(source_box)
        self.source_hint = QLabel("Width follows Target width plus a comfortable 10-character margin.")
        self.source_hint.setProperty("class", "muted")
        self.input_text = QPlainTextEdit()
        self.input_text.setPlaceholderText("Paste or type the text you want to format…")
        self.input_text.setMinimumWidth(360)
        self.input_text.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        source_layout.addWidget(self.source_hint)
        source_layout.addWidget(self.input_text, 1)
        self.workspace_splitter.addWidget(source_box)

        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(8, 0, 0, 0)
        controls_layout.setSpacing(10)

        rules_box = group_box("Line Rules")
        rules = QGridLayout(rules_box)
        rules.setHorizontalSpacing(10)
        rules.setVerticalSpacing(7)
        self.lines_per_page = numeric_spin(0, 200)
        self.min_words = numeric_spin(1, 50)
        self.max_words = numeric_spin(1, 80)
        self.target_width = numeric_spin(10, 200)
        self.tolerance = numeric_spin(0, 50)
        rule_items = (
            ("Lines per page", self.lines_per_page),
            ("Target width", self.target_width),
            ("Min words", self.min_words),
            ("Max words", self.max_words),
            ("Tolerance", self.tolerance),
        )
        for index, (label, field) in enumerate(rule_items):
            column = (index % 3) * 2
            row = index // 3
            rules.addWidget(QLabel(label), row, column)
            rules.addWidget(field, row, column + 1)
        rules.setColumnStretch(6, 1)
        controls_layout.addWidget(rules_box)

        map_box = group_box("Character Map")
        map_layout = QVBoxLayout(map_box)
        map_header = QHBoxLayout()
        self.direction = QComboBox()
        self.direction.addItem("Source → Result", REPLACEMENT_RESULT_TO_KEY)
        self.direction.addItem("Result → Source", REPLACEMENT_KEY_TO_RESULT)
        self.copy_source_button = secondary_button("Copy All Sources")
        self.copy_result_button = secondary_button("Copy All Results")
        map_header.addWidget(QLabel("Replacement direction"))
        map_header.addWidget(self.direction)
        map_header.addStretch(1)
        map_header.addWidget(self.copy_source_button)
        map_header.addWidget(self.copy_result_button)
        map_layout.addLayout(map_header)
        hint = QLabel("Click any Source or Result header to copy every value of that type. Cells support select, edit, copy, and paste.")
        hint.setProperty("class", "muted")
        map_layout.addWidget(hint)

        self.replacement_table = QTableWidget(0, self.map_pair_columns * 2)
        self.replacement_table.setHorizontalHeaderLabels(
            [label for _ in range(self.map_pair_columns) for label in ("Source", "Result")]
        )
        self.replacement_table.verticalHeader().setVisible(False)
        self.replacement_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.replacement_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.replacement_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.replacement_table.setAlternatingRowColors(True)
        self.replacement_table.setTabKeyNavigation(True)
        self.replacement_table.setMinimumHeight(260)
        map_layout.addWidget(self.replacement_table, 1)
        controls_layout.addWidget(map_box, 1)
        self.workspace_splitter.addWidget(controls)
        self.workspace_splitter.setStretchFactor(0, 0)
        self.workspace_splitter.setStretchFactor(1, 1)
        root.addWidget(self.workspace_splitter, 1)

        self.output_panel = group_box("Formatted Output")
        output_layout = QVBoxLayout(self.output_panel)
        output_actions = QHBoxLayout()
        output_note = QLabel("This output is always generated, even while this panel is hidden.")
        output_note.setProperty("class", "muted")
        self.copy_all_button = secondary_button("Copy All")
        close_output = secondary_button("Hide")
        output_actions.addWidget(output_note)
        output_actions.addStretch(1)
        output_actions.addWidget(self.copy_all_button)
        output_actions.addWidget(close_output)
        self.output_text = QPlainTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setMinimumHeight(180)
        self.output_text.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        output_layout.addLayout(output_actions)
        output_layout.addWidget(self.output_text, 1)
        self.output_panel.hide()
        root.addWidget(self.output_panel)

        self.format_button.clicked.connect(lambda: self.run_formatter(silent=False))
        self.view_button.toggled.connect(self.set_output_visible)
        close_output.clicked.connect(lambda: self.view_button.setChecked(False))
        self.quick_copy_button.clicked.connect(self.quick_copy)
        self.reset_queue_button.clicked.connect(self.reset_copy_queue)
        self.copy_all_button.clicked.connect(self.copy_output)
        self.save_button.clicked.connect(self.save_output)
        self.clear_button.clicked.connect(self.clear_text)
        self.copy_source_button.clicked.connect(lambda: self.copy_column(0))
        self.copy_result_button.clicked.connect(lambda: self.copy_column(1))
        self.replacement_table.horizontalHeader().sectionClicked.connect(self.copy_column)

    def _connect_live_updates(self) -> None:
        self.input_text.textChanged.connect(self.schedule_refresh)
        for field in (self.lines_per_page, self.min_words, self.max_words, self.target_width, self.tolerance):
            field.valueChanged.connect(lambda _value: self.settings_changed.emit())
            field.valueChanged.connect(self.schedule_refresh)
        self.target_width.valueChanged.connect(self.update_editor_widths)
        self.direction.currentIndexChanged.connect(lambda _index: self.settings_changed.emit())
        self.direction.currentIndexChanged.connect(self.schedule_refresh)
        self.replacement_table.itemChanged.connect(lambda _item: self.settings_changed.emit())
        self.replacement_table.itemChanged.connect(self.schedule_refresh)

    def load_config(self) -> None:
        self._loading = True
        formatter = self.config.get("formatter", {})
        self.lines_per_page.setValue(int(formatter.get("lines_per_page", 33)))
        self.min_words.setValue(int(formatter.get("min_words", 7)))
        self.max_words.setValue(int(formatter.get("max_words", 10)))
        self.target_width.setValue(int(formatter.get("target_width", 54)))
        self.tolerance.setValue(int(formatter.get("tolerance", 4)))
        direction = formatter.get("replacement_direction", REPLACEMENT_RESULT_TO_KEY)
        self.direction.setCurrentIndex(max(0, self.direction.findData(direction)))
        raw_map = formatter.get("character_map", formatter.get("replacements"))
        pairs = normalize_character_map(raw_map)
        self.map_pair_count = len(pairs)
        row_count = (len(pairs) + self.map_pair_columns - 1) // self.map_pair_columns
        self.replacement_table.clearContents()
        self.replacement_table.setRowCount(row_count)
        for index, (source, result) in enumerate(pairs):
            row = index // self.map_pair_columns
            column = (index % self.map_pair_columns) * 2
            self.replacement_table.setItem(row, column, QTableWidgetItem(source))
            self.replacement_table.setItem(row, column + 1, QTableWidgetItem(result))
            self.replacement_table.setRowHeight(row, 25)
        self.update_editor_widths()
        self.reset_copy_queue()
        self._loading = False

    def character_pairs(self) -> list[tuple[str, str]]:
        raw: list[tuple[str, str]] = []
        for index in range(self.map_pair_count):
            row = index // self.map_pair_columns
            column = (index % self.map_pair_columns) * 2
            source_item = self.replacement_table.item(row, column)
            result_item = self.replacement_table.item(row, column + 1)
            raw.append((source_item.text() if source_item else "", result_item.text() if result_item else ""))
        return normalize_character_map(raw)

    def collect_config(self) -> dict[str, Any]:
        pairs = self.character_pairs()
        return {
            "min_words": self.min_words.value(),
            "max_words": self.max_words.value(),
            "target_width": self.target_width.value(),
            "tolerance": self.tolerance.value(),
            "lines_per_page": self.lines_per_page.value(),
            "replacement_direction": self.direction.currentData(),
            "character_map": [{"source": source, "result": result} for source, result in pairs],
            "replacements": dict(pairs),
        }

    def settings(self) -> FormatterSettings:
        return FormatterSettings(
            min_words=self.min_words.value(),
            max_words=max(self.min_words.value(), self.max_words.value()),
            target_width=self.target_width.value(),
            tolerance=self.tolerance.value(),
            lines_per_page=self.lines_per_page.value(),
        )

    def schedule_refresh(self, *_: Any) -> None:
        if self._loading:
            return
        self.refresh_timer.start()

    def run_formatter(self, silent: bool = False) -> None:
        source = self.input_text.toPlainText()
        if not source.strip():
            if not silent:
                self.status("Enter text first.")
            self.output_text.clear()
            self.reset_copy_queue()
            return
        if self.min_words.value() > self.max_words.value():
            if not silent:
                QMessageBox.warning(self, "Check line rules", "Min words cannot be greater than max words.")
            return
        pairs = self.character_pairs()
        if self.direction.currentData() == REPLACEMENT_KEY_TO_RESULT:
            results = [result for _, result in pairs]
            if len(results) != len(set(results)) and not silent:
                QMessageBox.warning(self, "Duplicate results", "Duplicate result characters were found. Later rows will win.")
        mapping = build_replacement_mapping(pairs, self.direction.currentData())
        processed = apply_replacements(source, mapping)
        formatted, oversized = format_text(processed, self.settings())
        self.output_text.setPlainText(formatted)
        self.reset_copy_queue()
        if not silent:
            message = f"Formatted {len(formatted.splitlines())} output lines."
            if oversized:
                message += " One paragraph exceeds the page limit."
            self.status(message)
        LOGGER.info("Formatted text into %d copy section(s)", len(self.copy_sections))

    def update_editor_widths(self, *_: Any) -> None:
        character_width = max(7, self.input_text.fontMetrics().averageCharWidth())
        desired = min(920, max(380, ((self.target_width.value() + 10) * character_width) + 44))
        self.input_text.setMaximumWidth(desired)
        self.output_text.setMaximumWidth(desired)
        self.output_panel.setMaximumWidth(desired + 36)
        if hasattr(self, "workspace_splitter"):
            self.workspace_splitter.setSizes([desired + 24, max(560, self.width() - desired - 24)])

    def set_output_visible(self, visible: bool) -> None:
        self.output_panel.setVisible(visible)
        self.view_button.setText("Hide Formatted Output" if visible else "View Formatted Output")

    def reset_copy_queue(self) -> None:
        self.copy_sections = split_formatted_sections(self.output_text.toPlainText())
        self.copy_index = 0
        self.update_copy_progress()

    def update_copy_progress(self) -> None:
        total = len(self.copy_sections)
        if not total:
            self.queue_progress.setText("Quick Copy is ready when formatted sections are generated.")
            self.queue_next.setText("0 sections copied")
            self.queue_timeline.setRange(0, 1)
            self.queue_timeline.setValue(0)
            self.quick_copy_button.setText("Quick Copy Next Section")
            self.quick_copy_button.setEnabled(False)
            self.reset_queue_button.setEnabled(False)
            return
        self.queue_timeline.setRange(0, total)
        self.queue_timeline.setValue(self.copy_index)
        self.reset_queue_button.setEnabled(True)
        if self.copy_index >= total:
            self.queue_progress.setText(f"Complete - all {total} sections copied")
            self.queue_next.setText(f"{total} of {total} copied. Reset the queue to start again.")
            self.quick_copy_button.setText("All Sections Copied")
            self.quick_copy_button.setEnabled(False)
            return
        limit = max(1, self.lines_per_page.value())
        start = (self.copy_index * limit) + 1
        section_lines = self.copy_sections[self.copy_index].splitlines()
        content_lines = [line for line in section_lines if line != PAGE_END_MARKER]
        if self.copy_index == total - 1 and content_lines and content_lines[-1] == PARAGRAPH_SPACER:
            content_lines.pop()
        content_count = len(content_lines)
        end = start + max(0, content_count - 1)
        self.queue_progress.setText(f"Section {self.copy_index + 1} of {total} ready")
        self.queue_next.setText(f"{self.copy_index} of {total} copied  |  Next: lines {start}-{end}")
        self.quick_copy_button.setText(f"Copy Section {self.copy_index + 1}")
        self.quick_copy_button.setEnabled(True)

    def quick_copy(self) -> None:
        if not self.copy_sections:
            self.run_formatter(silent=False)
        if self.copy_index >= len(self.copy_sections):
            return
        QApplication.clipboard().setText(self.copy_sections[self.copy_index])
        copied = self.copy_index + 1
        self.copy_index += 1
        self.update_copy_progress()
        self.status(f"Copied section {copied} of {len(self.copy_sections)}.")

    def copy_column(self, column: int) -> None:
        logical_column = column % 2
        values: list[str] = []
        for index in range(self.map_pair_count):
            row = index // self.map_pair_columns
            physical_column = ((index % self.map_pair_columns) * 2) + logical_column
            item = self.replacement_table.item(row, physical_column)
            values.append(item.text() if item else "")
        QApplication.clipboard().setText("\n".join(values))
        name = "Source" if logical_column == 0 else "Result"
        self.status(f"Copied all {name} values.")

    def copy_output(self) -> None:
        content = self.output_text.toPlainText().strip()
        if not content:
            self.status("Nothing to copy.")
            return
        QApplication.clipboard().setText(content)
        self.status("Complete formatted output copied.")

    def save_output(self) -> None:
        content = self.output_text.toPlainText()
        if not content.strip():
            self.status("Nothing to save.")
            return
        output_dir = Path(self.config.get("output_dir", str(default_output_dir())))
        output_dir.mkdir(parents=True, exist_ok=True)
        default_name = output_dir / f"formatted_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        path, _ = QFileDialog.getSaveFileName(self, "Save formatted text", str(default_name), "Text files (*.txt)")
        if path:
            Path(path).write_text(content, encoding="utf-8")
            self.status(f"Saved {Path(path).name}.")

    def clear_text(self) -> None:
        if self.input_text.toPlainText().strip():
            answer = QMessageBox.question(
                self,
                "Clear text",
                "Clear source and generated output?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.input_text.clear()
        self.output_text.clear()
        self.view_button.setChecked(False)
        self.reset_copy_queue()
        self.status("Text cleared.")


class SingleImageTab(QWidget):
    def __init__(
        self,
        config: dict[str, Any],
        status: Callable[[str], None],
        run_task: TaskRunner,
    ) -> None:
        super().__init__()
        self.config = config
        self.status = status
        self.run_task = run_task
        self.line_color = tuple(config.get("image", {}).get("line_color", [0, 0, 0]))
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        form_box = group_box("Input")
        form = QGridLayout(form_box)
        self.input_path = QLineEdit()
        self.input_path.setPlaceholderText("Choose a PNG, JPG, JPEG, or WebP image.")
        browse = secondary_button("Browse")
        browse.clicked.connect(self.browse_input)
        form.addWidget(QLabel("Image"), 0, 0)
        form.addWidget(self.input_path, 0, 1)
        form.addWidget(browse, 0, 2)

        self.output_dir = QLineEdit(str(self.config.get("output_dir", default_output_dir())))
        choose_dir = secondary_button("Choose")
        choose_dir.clicked.connect(self.choose_output_dir)
        form.addWidget(QLabel("Output folder"), 1, 0)
        form.addWidget(self.output_dir, 1, 1)
        form.addWidget(choose_dir, 1, 2)

        self.output_name = QLineEdit()
        self.output_name.setPlaceholderText("Auto: source_processed.png")
        form.addWidget(QLabel("File name"), 2, 0)
        form.addWidget(self.output_name, 2, 1, 1, 2)
        form.setColumnStretch(1, 1)
        root.addWidget(form_box)

        settings_box = group_box("Processing")
        settings = QGridLayout(settings_box)
        image_root = self.config.get("image", {})
        image_config = image_root.get("single", image_root)
        self.connect_lines = QCheckBox("Connect yellow guide strokes")
        self.connect_lines.setChecked(bool(image_config.get("connect_lines", True)))
        self.remove_yellow = QCheckBox("Remove yellow guide pixels")
        self.remove_yellow.setChecked(bool(image_config.get("remove_yellow", True)))
        self.thickness = numeric_spin(1, 80)
        self.thickness.setValue(int(image_config.get("line_thickness", 7)))
        self.tolerance = numeric_spin(1, 80)
        self.tolerance.setValue(int(image_config.get("y_tolerance", 2)))
        self.color_text = QLineEdit(color_to_text(self.line_color))
        pick_color = secondary_button("Pick")
        pick_color.clicked.connect(self.pick_color)
        settings.addWidget(self.connect_lines, 0, 0, 1, 2)
        settings.addWidget(self.remove_yellow, 1, 0, 1, 2)
        settings.addWidget(QLabel("Thickness"), 2, 0)
        settings.addWidget(self.thickness, 2, 1)
        settings.addWidget(QLabel("Y tolerance"), 3, 0)
        settings.addWidget(self.tolerance, 3, 1)
        settings.addWidget(QLabel("Line color"), 4, 0)
        settings.addWidget(self.color_text, 4, 1)
        settings.addWidget(pick_color, 4, 2)
        root.addWidget(settings_box)

        actions = QHBoxLayout()
        self.run_button = primary_button("Process Image")
        self.run_button.clicked.connect(self.process)
        actions.addWidget(self.run_button)
        actions.addStretch(1)
        root.addLayout(actions)
        root.addStretch(1)

    def browse_input(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose image",
            str(configured_import_dir(self.config)),
            "Images (*.png *.jpg *.jpeg *.webp);;All files (*.*)",
        )
        if path:
            self.input_path.setText(path)
            if not self.output_name.text().strip():
                self.output_name.setText(f"{Path(path).stem}_processed.png")

    def choose_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose output folder", self.output_dir.text())
        if path:
            self.output_dir.setText(path)
            self.config["output_dir"] = path

    def pick_color(self) -> None:
        color = QColorDialog.getColor(QColor(*parse_color_text(self.color_text.text())), self, "Pick line color")
        if color.isValid():
            self.line_color = (color.red(), color.green(), color.blue())
            self.color_text.setText(color_to_text(self.line_color))

    def image_settings(self) -> ImageProcessingSettings:
        return ImageProcessingSettings(
            line_thickness=self.thickness.value(),
            y_tolerance=self.tolerance.value(),
            line_color=parse_color_text(self.color_text.text()),
            connect_lines=self.connect_lines.isChecked(),
            remove_yellow=self.remove_yellow.isChecked(),
        )

    def process(self) -> None:
        source = Path(self.input_path.text().strip())
        if not source.exists():
            self.status("Choose an existing image first.")
            return
        if not self.connect_lines.isChecked() and not self.remove_yellow.isChecked():
            self.status("Enable at least one processing action.")
            return
        try:
            settings = self.image_settings()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid color", str(exc))
            return
        output_dir = Path(self.output_dir.text().strip() or str(default_output_dir()))
        name = self.output_name.text().strip() or f"{source.stem}_processed.png"
        output_path = ensure_ext(output_dir / name, ".png")

        self.run_button.setEnabled(False)
        self.status("Processing image...")

        def done(result: Any) -> None:
            self.run_button.setEnabled(True)
            self.status(f"Saved {Path(result).name}.")

        def failed(message: str) -> None:
            self.run_button.setEnabled(True)
            QMessageBox.warning(self, "Image processing failed", message)
            self.status("Image processing failed.")

        self.run_task(process_single_image, done, failed, source, output_path, settings)


class StitchTab(QWidget):
    def __init__(
        self,
        config: dict[str, Any],
        status: Callable[[str], None],
        run_task: TaskRunner,
    ) -> None:
        super().__init__()
        self.config = config
        self.status = status
        self.run_task = run_task
        self.line_color = tuple(config.get("image", {}).get("line_color", [0, 0, 0]))
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        queue_box = group_box("Image Queue")
        queue_layout = QVBoxLayout(queue_box)
        self.files = QListWidget()
        self.files.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.files.setDragDropMode(QAbstractItemView.InternalMove)
        self.files.setDefaultDropAction(Qt.MoveAction)
        self.files.setMinimumHeight(180)
        queue_layout.addWidget(self.files)

        import_folder = QHBoxLayout()
        self.import_dir = QLineEdit(str(configured_import_dir(self.config)))
        self.import_dir.setPlaceholderText("Folder opened by Add Images")
        choose_import_dir = secondary_button("Choose")
        choose_import_dir.clicked.connect(self.choose_import_dir)
        import_folder.addWidget(QLabel("Default import folder"))
        import_folder.addWidget(self.import_dir, 1)
        import_folder.addWidget(choose_import_dir)
        queue_layout.addLayout(import_folder)

        queue_actions = QHBoxLayout()
        add = secondary_button("Add Images")
        remove = secondary_button("Remove")
        move_up = secondary_button("Move Up")
        move_down = secondary_button("Move Down")
        clear = secondary_button("Clear")
        add.clicked.connect(self.add_images)
        remove.clicked.connect(self.remove_selected)
        move_up.clicked.connect(lambda: self.move_selected(-1))
        move_down.clicked.connect(lambda: self.move_selected(1))
        clear.clicked.connect(self.clear_queue)
        for button in (add, remove, move_up, move_down, clear):
            queue_actions.addWidget(button)
        queue_actions.addStretch(1)
        queue_layout.addLayout(queue_actions)
        root.addWidget(queue_box)

        settings_box = group_box("Export Settings")
        settings_grid = QGridLayout(settings_box)
        image_root = self.config.get("image", {})
        image_config = image_root.get("stitch", image_root)
        self.output_dir = QLineEdit(str(self.config.get("output_dir", default_output_dir())))
        choose_dir = secondary_button("Choose")
        choose_dir.clicked.connect(self.choose_output_dir)
        self.output_name = QLineEdit(str(image_root.get("output_name", "handwriting.pdf")))
        self.export_mode = QComboBox()
        self.export_mode.addItem("Paginated PDF", ExportMode.PDF)
        self.export_mode.addItem("Stitched PNG", ExportMode.STITCHED_PNG)
        self.export_mode.addItem("A4 Printable PNG", ExportMode.A4_PNG)
        saved_mode = str(image_root.get("export_mode", ExportMode.PDF.value))
        saved_index = self.export_mode.findData(ExportMode(saved_mode)) if saved_mode in {mode.value for mode in ExportMode} else 0
        self.export_mode.setCurrentIndex(max(0, saved_index))
        self.export_mode.currentIndexChanged.connect(self.update_default_name)
        self.overlap = numeric_spin(0, 5000)
        self.overlap.setValue(int(image_config.get("overlap_px", 0)))
        self.connect_lines = QCheckBox("Connect yellow guide strokes")
        self.connect_lines.setChecked(bool(image_config.get("connect_lines", True)))
        self.remove_yellow = QCheckBox("Remove yellow guide pixels")
        self.remove_yellow.setChecked(bool(image_config.get("remove_yellow", True)))
        self.delete_originals = QCheckBox("Delete originals after successful export")
        self.delete_originals.setChecked(bool(image_root.get("delete_originals", False)))
        self.confirm_delete_originals = QCheckBox("Ask before deleting originals")
        self.confirm_delete_originals.setChecked(bool(image_root.get("confirm_delete_originals", True)))
        self.confirm_delete_originals.setEnabled(self.delete_originals.isChecked())
        self.delete_originals.toggled.connect(self.confirm_delete_originals.setEnabled)
        self.thickness = numeric_spin(1, 80)
        self.thickness.setValue(int(image_config.get("line_thickness", 7)))
        self.tolerance = numeric_spin(1, 80)
        self.tolerance.setValue(int(image_config.get("y_tolerance", 2)))
        self.color_text = QLineEdit(color_to_text(self.line_color))
        pick_color = secondary_button("Pick")
        pick_color.clicked.connect(self.pick_color)

        settings_grid.addWidget(QLabel("Output folder"), 0, 0)
        settings_grid.addWidget(self.output_dir, 0, 1)
        settings_grid.addWidget(choose_dir, 0, 2)
        settings_grid.addWidget(QLabel("File name"), 1, 0)
        settings_grid.addWidget(self.output_name, 1, 1, 1, 2)
        settings_grid.addWidget(QLabel("Export mode"), 2, 0)
        settings_grid.addWidget(self.export_mode, 2, 1, 1, 2)
        settings_grid.addWidget(QLabel("Overlap"), 3, 0)
        settings_grid.addWidget(self.overlap, 3, 1)
        settings_grid.addWidget(self.connect_lines, 4, 0, 1, 2)
        settings_grid.addWidget(self.remove_yellow, 5, 0, 1, 2)
        settings_grid.addWidget(self.delete_originals, 6, 0, 1, 2)
        settings_grid.addWidget(self.confirm_delete_originals, 7, 0, 1, 2)
        settings_grid.addWidget(QLabel("Thickness"), 8, 0)
        settings_grid.addWidget(self.thickness, 8, 1)
        settings_grid.addWidget(QLabel("Y tolerance"), 9, 0)
        settings_grid.addWidget(self.tolerance, 9, 1)
        settings_grid.addWidget(QLabel("Line color"), 10, 0)
        settings_grid.addWidget(self.color_text, 10, 1)
        settings_grid.addWidget(pick_color, 10, 2)
        settings_grid.setColumnStretch(1, 1)
        root.addWidget(settings_box)

        actions = QHBoxLayout()
        self.run_button = primary_button("Run Export")
        self.run_button.clicked.connect(self.process)
        actions.addWidget(self.run_button)
        actions.addStretch(1)
        root.addLayout(actions)
        root.addStretch(1)

    def update_default_name(self) -> None:
        current = self.output_name.text().strip()
        mode = self.export_mode.currentData()
        if not current or current in {"handwriting.pdf", "stitched.png", "a4_printable.png"}:
            if mode == ExportMode.PDF:
                self.output_name.setText("handwriting.pdf")
            elif mode == ExportMode.A4_PNG:
                self.output_name.setText("a4_printable.png")
            else:
                self.output_name.setText("stitched.png")

    def add_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Add images",
            str(Path(self.import_dir.text().strip()).expanduser())
            if self.import_dir.text().strip()
            else str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.webp);;All files (*.*)",
        )
        existing = {self.files.item(row).text() for row in range(self.files.count())}
        added = 0
        for path in paths:
            if path not in existing:
                self.files.addItem(path)
                existing.add(path)
                added += 1
        if added:
            self.status(f"Added {added} image(s).")

    def choose_import_dir(self) -> None:
        start = self.import_dir.text().strip() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Choose default import folder", start)
        if path:
            self.import_dir.setText(path)
            image_config = self.config.setdefault("image", {})
            image_config["import_dir"] = path
            self.status("Default image import folder updated.")

    def remove_selected(self) -> None:
        for item in self.files.selectedItems():
            self.files.takeItem(self.files.row(item))
        self.status("Removed selected image(s).")

    def clear_queue(self) -> None:
        if self.files.count() == 0:
            return
        answer = QMessageBox.question(
            self,
            "Clear queue",
            "Remove all queued images?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.files.clear()
            self.status("Queue cleared.")

    def move_selected(self, direction: int) -> None:
        selected_rows = sorted({self.files.row(item) for item in self.files.selectedItems()})
        if not selected_rows:
            return
        rows: Sequence[int] = selected_rows if direction < 0 else reversed(selected_rows)
        for row in rows:
            new_row = row + direction
            if 0 <= new_row < self.files.count():
                item = self.files.takeItem(row)
                self.files.insertItem(new_row, item)
                item.setSelected(True)

    def choose_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose output folder", self.output_dir.text())
        if path:
            self.output_dir.setText(path)
            self.config["output_dir"] = path

    def pick_color(self) -> None:
        color = QColorDialog.getColor(QColor(*parse_color_text(self.color_text.text())), self, "Pick line color")
        if color.isValid():
            self.line_color = (color.red(), color.green(), color.blue())
            self.color_text.setText(color_to_text(self.line_color))

    def queued_paths(self) -> list[Path]:
        return [Path(self.files.item(row).text()) for row in range(self.files.count())]

    def image_settings(self) -> ImageProcessingSettings:
        return ImageProcessingSettings(
            line_thickness=self.thickness.value(),
            y_tolerance=self.tolerance.value(),
            line_color=parse_color_text(self.color_text.text()),
            overlap_px=self.overlap.value(),
            connect_lines=self.connect_lines.isChecked(),
            remove_yellow=self.remove_yellow.isChecked(),
        )

    def process(self) -> None:
        paths = self.queued_paths()
        if not paths:
            self.status("Add images first.")
            return
        missing = [str(path) for path in paths if not path.exists()]
        if missing:
            QMessageBox.warning(self, "Missing files", "One or more queued images no longer exist.")
            return
        try:
            settings = self.image_settings()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid color", str(exc))
            return

        mode = self.export_mode.currentData()
        output_dir = Path(self.output_dir.text().strip() or str(default_output_dir()))
        name = self.output_name.text().strip()
        if not name:
            name = "handwriting.pdf" if mode == ExportMode.PDF else "stitched.png"
        output_path = output_dir / name
        if mode == ExportMode.PDF:
            output_path = ensure_ext(output_path, ".pdf")
        else:
            output_path = ensure_ext(output_path, ".png")

        delete_originals = self.delete_originals.isChecked()
        if delete_originals and self.confirm_delete_originals.isChecked():
            answer = QMessageBox.warning(
                self,
                "Delete originals",
                "Original queued files will be deleted after export succeeds. Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        self.run_button.setEnabled(False)
        self.status("Running image export...")

        def done(result: Any) -> None:
            self.run_button.setEnabled(True)
            if delete_originals:
                self.files.clear()
            self.status(f"Saved {Path(result).name}.")

        def failed(message: str) -> None:
            self.run_button.setEnabled(True)
            QMessageBox.warning(self, "Export failed", message)
            self.status("Export failed.")

        self.run_task(
            process_stitch_export,
            done,
            failed,
            paths,
            output_path,
            mode,
            settings,
            delete_originals,
        )


class ImageToolsPage(QWidget):
    settings_changed = Signal()

    def __init__(
        self,
        config: dict[str, Any],
        status: Callable[[str], None],
        run_task: TaskRunner,
    ) -> None:
        super().__init__()
        self.config = config
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.single_tab = SingleImageTab(config, status, run_task)
        self.stitch_tab = StitchTab(config, status, run_task)
        self.tabs.addTab(self.single_tab, "Single Image")
        self.tabs.addTab(self.stitch_tab, "Stitch Export")
        preferred = str(config.get("image", {}).get("default_tab", "stitch"))
        self.tabs.setCurrentIndex(1 if preferred == "stitch" else 0)
        layout.addWidget(self.tabs)

        tracked = (
            self.single_tab.thickness,
            self.single_tab.tolerance,
            self.single_tab.color_text,
            self.single_tab.connect_lines,
            self.single_tab.remove_yellow,
            self.single_tab.output_dir,
            self.single_tab.output_name,
            self.stitch_tab.thickness,
            self.stitch_tab.tolerance,
            self.stitch_tab.color_text,
            self.stitch_tab.overlap,
            self.stitch_tab.connect_lines,
            self.stitch_tab.remove_yellow,
            self.stitch_tab.delete_originals,
            self.stitch_tab.confirm_delete_originals,
            self.stitch_tab.import_dir,
            self.stitch_tab.export_mode,
            self.stitch_tab.output_name,
            self.stitch_tab.output_dir,
        )
        for widget in tracked:
            if isinstance(widget, QSpinBox):
                widget.valueChanged.connect(lambda _value: self.settings_changed.emit())
            elif isinstance(widget, QCheckBox):
                widget.toggled.connect(lambda _checked: self.settings_changed.emit())
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(lambda _index: self.settings_changed.emit())
            elif isinstance(widget, QLineEdit):
                widget.textChanged.connect(lambda _text: self.settings_changed.emit())
        self.tabs.currentChanged.connect(lambda _index: self.settings_changed.emit())
        self.single_tab.output_dir.textChanged.connect(
            lambda text: self.stitch_tab.output_dir.setText(text)
            if self.stitch_tab.output_dir.text() != text
            else None
        )
        self.stitch_tab.output_dir.textChanged.connect(
            lambda text: self.single_tab.output_dir.setText(text)
            if self.single_tab.output_dir.text() != text
            else None
        )

    def load_config(self) -> None:
        root = self.config.get("image", {})
        single = root.get("single", root)
        stitch = root.get("stitch", root)
        self.single_tab.thickness.setValue(int(single.get("line_thickness", 7)))
        self.single_tab.tolerance.setValue(int(single.get("y_tolerance", 2)))
        self.single_tab.color_text.setText(color_to_text(tuple(single.get("line_color", [0, 0, 0]))))
        self.single_tab.connect_lines.setChecked(bool(single.get("connect_lines", True)))
        self.single_tab.remove_yellow.setChecked(bool(single.get("remove_yellow", True)))
        self.stitch_tab.thickness.setValue(int(stitch.get("line_thickness", 7)))
        self.stitch_tab.tolerance.setValue(int(stitch.get("y_tolerance", 2)))
        self.stitch_tab.color_text.setText(color_to_text(tuple(stitch.get("line_color", [0, 0, 0]))))
        self.stitch_tab.overlap.setValue(int(stitch.get("overlap_px", 0)))
        self.stitch_tab.connect_lines.setChecked(bool(stitch.get("connect_lines", True)))
        self.stitch_tab.remove_yellow.setChecked(bool(stitch.get("remove_yellow", True)))
        self.stitch_tab.delete_originals.setChecked(bool(root.get("delete_originals", False)))
        self.stitch_tab.confirm_delete_originals.setChecked(bool(root.get("confirm_delete_originals", True)))
        self.stitch_tab.import_dir.setText(str(root.get("import_dir", Path.home())))
        self.stitch_tab.output_dir.setText(str(self.config.get("output_dir", default_output_dir())))
        self.single_tab.output_dir.setText(str(self.config.get("output_dir", default_output_dir())))
        self.single_tab.output_name.setText(str(root.get("single_output_name", "")))
        self.stitch_tab.output_name.setText(str(root.get("output_name", "handwriting.pdf")))
        mode_value = str(root.get("export_mode", ExportMode.PDF.value))
        if mode_value in {mode.value for mode in ExportMode}:
            self.stitch_tab.export_mode.setCurrentIndex(
                max(0, self.stitch_tab.export_mode.findData(ExportMode(mode_value)))
            )
        self.tabs.setCurrentIndex(1 if str(root.get("default_tab", "stitch")) == "stitch" else 0)

    def collect_config(self) -> dict[str, Any]:
        single = {
            "line_thickness": self.single_tab.thickness.value(),
            "y_tolerance": self.single_tab.tolerance.value(),
            "line_color": list(parse_color_text(self.single_tab.color_text.text())),
            "connect_lines": self.single_tab.connect_lines.isChecked(),
            "remove_yellow": self.single_tab.remove_yellow.isChecked(),
        }
        stitch = {
            "line_thickness": self.stitch_tab.thickness.value(),
            "y_tolerance": self.stitch_tab.tolerance.value(),
            "line_color": list(parse_color_text(self.stitch_tab.color_text.text())),
            "overlap_px": self.stitch_tab.overlap.value(),
            "connect_lines": self.stitch_tab.connect_lines.isChecked(),
            "remove_yellow": self.stitch_tab.remove_yellow.isChecked(),
        }
        mode = self.stitch_tab.export_mode.currentData()
        return {
            **stitch,
            "single": single,
            "stitch": stitch,
            "default_tab": "stitch" if self.tabs.currentIndex() == 1 else "single",
            "export_mode": mode.value if isinstance(mode, ExportMode) else str(mode),
            "output_name": self.stitch_tab.output_name.text().strip(),
            "single_output_name": self.single_tab.output_name.text().strip(),
            "delete_originals": self.stitch_tab.delete_originals.isChecked(),
            "confirm_delete_originals": self.stitch_tab.confirm_delete_originals.isChecked(),
            "import_dir": self.stitch_tab.import_dir.text().strip() or str(Path.home()),
        }


class HotkeyListenerThread(QThread):
    toggle_requested = Signal()
    step_requested = Signal()
    listening = Signal()
    listener_error = Signal(str)
    stopped = Signal()

    def __init__(self, toggle_hotkey: str, step_hotkey: str) -> None:
        super().__init__()
        self.toggle_hotkey = toggle_hotkey
        self.step_hotkey = step_hotkey
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        try:
            import keyboard
        except Exception as exc:
            self.listener_error.emit(f"Could not load keyboard hotkeys: {exc}")
            return

        toggle_handle = None
        step_handle = None
        try:
            toggle_handle = keyboard.add_hotkey(self.toggle_hotkey, lambda: self.toggle_requested.emit())
            step_handle = keyboard.add_hotkey(self.step_hotkey, lambda: self.step_requested.emit())
            self.listening.emit()
            while not self._stop_requested:
                self.msleep(100)
        except Exception as exc:
            self.listener_error.emit(f"Hotkey listener failed: {exc}")
        finally:
            for handle in (toggle_handle, step_handle):
                if handle is not None:
                    try:
                        keyboard.remove_hotkey(handle)
                    except Exception:
                        LOGGER.debug("Could not remove hotkey", exc_info=True)
            self.stopped.emit()


class QuickCopyHotkeyThread(QThread):
    copy_requested = Signal()
    listening = Signal()
    listener_error = Signal(str)
    stopped = Signal()

    def __init__(self, hotkey: str) -> None:
        super().__init__()
        self.hotkey = hotkey
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        try:
            import keyboard
        except Exception as exc:
            self.listener_error.emit(f"Could not load keyboard hotkeys: {exc}")
            return

        handle = None
        try:
            handle = keyboard.add_hotkey(self.hotkey, lambda: self.copy_requested.emit())
            self.listening.emit()
            while not self._stop_requested:
                self.msleep(100)
        except Exception as exc:
            self.listener_error.emit(f"Could not register '{self.hotkey}': {exc}")
        finally:
            if handle is not None:
                try:
                    keyboard.remove_hotkey(handle)
                except Exception:
                    LOGGER.debug("Could not remove Quick Copy hotkey", exc_info=True)
            self.stopped.emit()


class MacroPage(QWidget):
    def __init__(
        self,
        config: dict[str, Any],
        status: Callable[[str], None],
        run_task: TaskRunner,
        request_save: Callable[[], None],
    ) -> None:
        super().__init__()
        self.config = config
        self.status = status
        self.run_task = run_task
        self.request_save = request_save
        self.listener: HotkeyListenerThread | None = None
        self.step_running = False
        self._build()
        self._load_config()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        settings_box = group_box("Training Export Macro")
        form = QFormLayout(settings_box)
        self.prefix = QLineEdit()
        self.counter = QSpinBox()
        self.counter.setRange(0, 999999)
        self.delay = QDoubleSpinBox()
        self.delay.setRange(0, 20)
        self.delay.setSingleStep(0.5)
        self.delay.setSuffix(" sec")
        self.toggle_hotkey = QLineEdit()
        self.step_hotkey = QLineEdit()
        self.enabled = QCheckBox("Macro enabled")
        form.addRow("File prefix", self.prefix)
        form.addRow("Next counter", self.counter)
        form.addRow("Manual run delay", self.delay)
        form.addRow("Toggle hotkey", self.toggle_hotkey)
        form.addRow("Run step hotkey", self.step_hotkey)
        form.addRow("", self.enabled)
        root.addWidget(settings_box)

        self.state_label = QLabel("Hotkeys are stopped.")
        self.state_label.setProperty("class", "muted")
        root.addWidget(self.state_label)

        actions = QHBoxLayout()
        self.arm_button = primary_button("Start Hotkeys")
        self.stop_button = secondary_button("Stop Hotkeys")
        self.run_button = secondary_button("Run Step After Delay")
        self.save_button = secondary_button("Save Macro Settings")
        self.stop_button.setEnabled(False)
        self.arm_button.clicked.connect(self.start_hotkeys)
        self.stop_button.clicked.connect(self.stop_hotkeys)
        self.run_button.clicked.connect(self.confirm_manual_step)
        self.save_button.clicked.connect(self.save_settings)
        for button in (self.arm_button, self.stop_button, self.run_button, self.save_button):
            actions.addWidget(button)
        actions.addStretch(1)
        root.addLayout(actions)

        note = QLabel(
            "Use hotkeys while the target app is focused. F9 runs one export step only when Macro enabled is checked."
        )
        note.setWordWrap(True)
        note.setProperty("class", "muted")
        root.addWidget(note)
        root.addStretch(1)

    def _load_config(self) -> None:
        macro = self.config.get("macro", {})
        self.prefix.setText(str(macro.get("prefix", "newtrainingdata")))
        self.counter.setValue(int(macro.get("counter", 161)))
        self.delay.setValue(float(macro.get("initial_delay_seconds", 2.0)))
        self.toggle_hotkey.setText(str(macro.get("toggle_hotkey", "F8")))
        self.step_hotkey.setText(str(macro.get("step_hotkey", "F9")))
        self.enabled.setChecked(False)

    def collect_config(self) -> dict[str, Any]:
        return {
            "prefix": self.prefix.text().strip() or "newtrainingdata",
            "counter": self.counter.value(),
            "initial_delay_seconds": self.delay.value(),
            "toggle_hotkey": self.toggle_hotkey.text().strip() or "F8",
            "step_hotkey": self.step_hotkey.text().strip() or "F9",
        }

    def save_settings(self) -> None:
        self.config["macro"] = self.collect_config()
        self.request_save()
        self.status("Macro settings saved.")

    def start_hotkeys(self) -> None:
        if self.listener and self.listener.isRunning():
            return
        answer = QMessageBox.information(
            self,
            "Start hotkeys",
            "Hotkeys will listen globally until stopped. Use them only when your target app is focused.",
            QMessageBox.Ok | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Ok:
            return
        self.listener = HotkeyListenerThread(
            self.toggle_hotkey.text().strip() or "F8",
            self.step_hotkey.text().strip() or "F9",
        )
        self.listener.toggle_requested.connect(self.toggle_enabled)
        self.listener.step_requested.connect(lambda: self.run_step(delay_seconds=0.0))
        self.listener.listening.connect(self.hotkeys_started)
        self.listener.listener_error.connect(self.hotkeys_error)
        self.listener.stopped.connect(self.hotkeys_stopped)
        self.listener.start()
        self.status("Starting hotkeys...")

    def stop_hotkeys(self) -> None:
        if self.listener:
            self.listener.stop()
            self.listener.wait(1500)
        self.hotkeys_stopped()

    def hotkeys_started(self) -> None:
        self.arm_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.state_label.setText("Hotkeys are running.")
        self.status("Hotkeys running.")

    def hotkeys_stopped(self) -> None:
        self.arm_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.state_label.setText("Hotkeys are stopped.")
        self.status("Hotkeys stopped.")

    def hotkeys_error(self, message: str) -> None:
        QMessageBox.warning(self, "Hotkey error", message)
        self.hotkeys_stopped()

    def toggle_enabled(self) -> None:
        self.enabled.setChecked(not self.enabled.isChecked())
        self.status("Macro enabled." if self.enabled.isChecked() else "Macro disabled.")

    def confirm_manual_step(self) -> None:
        answer = QMessageBox.question(
            self,
            "Run macro step",
            "The app will send export keystrokes after the delay. Switch to the target window before the delay ends.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.enabled.setChecked(True)
            self.run_step(delay_seconds=self.delay.value())

    def run_step(self, delay_seconds: float) -> None:
        if self.step_running:
            self.status("Macro step already running.")
            return
        if not self.enabled.isChecked():
            self.status("Macro is disabled.")
            return
        settings = MacroSettings(
            prefix=self.prefix.text().strip() or "newtrainingdata",
            counter=self.counter.value(),
            initial_delay_seconds=delay_seconds,
        )
        macro = TrainingExportMacro(settings)
        self.step_running = True
        self.run_button.setEnabled(False)
        self.status("Running macro step...")

        def done(result: Any) -> None:
            self.step_running = False
            self.run_button.setEnabled(True)
            self.counter.setValue(result.next_counter)
            self.config["macro"] = self.collect_config()
            self.request_save()
            self.status(f"Exported {result.filename}.")

        def failed(message: str) -> None:
            self.step_running = False
            self.run_button.setEnabled(True)
            QMessageBox.warning(self, "Macro failed", message)
            self.status("Macro failed.")

        self.run_task(macro.run_step, done, failed)

    def shutdown(self) -> None:
        if self.listener and self.listener.isRunning():
            self.listener.stop()
            self.listener.wait(1500)


class SettingsPage(QWidget):
    settings_changed = Signal()
    appearance_changed = Signal()
    quick_copy_hotkey_changed = Signal(str, bool)
    reset_requested = Signal()
    apply_window_size_requested = Signal(int, int)

    def __init__(
        self,
        config: dict[str, Any],
        status: Callable[[str], None],
        run_task: TaskRunner,
    ) -> None:
        super().__init__()
        self.config = config
        self.status = status
        self.run_task = run_task
        self._build()
        self.load_config()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        scroll.setWidget(body)
        outer.addWidget(scroll)

        root = QVBoxLayout(body)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(14)

        appearance_box = group_box("Colour Scheme")
        appearance_box.setObjectName("appearanceBox")
        appearance = QGridLayout(appearance_box)
        appearance.setHorizontalSpacing(10)
        appearance.setVerticalSpacing(8)
        self.theme = QComboBox()
        self.theme.addItem("System default", "system")
        self.theme.addItem("Light", "light")
        self.theme.addItem("Dark", "dark")
        self.theme.addItem("Custom", "custom")
        self.theme.setMaximumWidth(280)
        appearance.addWidget(QLabel("Theme"), 0, 0)
        appearance.addWidget(self.theme, 0, 1, 1, 2)

        self.palette_family = QComboBox()
        for family in PALETTE_FAMILIES:
            self.palette_family.addItem(family, family)
        self.palette_family.setMaximumWidth(280)
        appearance.addWidget(QLabel("Palette colour"), 1, 0)
        appearance.addWidget(self.palette_family, 1, 1, 1, 2)

        self.palette_preset = QComboBox()
        self.refresh_palette_presets()
        self.palette_preset.setMaximumWidth(280)
        self.online_palette_button = secondary_button("Random Online Palette")
        self.online_palette_button.setFixedWidth(170)
        appearance.addWidget(QLabel("Whole palette"), 2, 0)
        appearance.addWidget(self.palette_preset, 2, 1, 1, 2)
        appearance.addWidget(self.online_palette_button, 2, 3)

        self.palette_input = QLineEdit()
        self.palette_input.setPlaceholderText("Paste 4 hex colours or a palette-generator URL")
        self.palette_input.setClearButtonEnabled(True)
        self.palette_apply = secondary_button("Apply Palette")
        self.palette_apply.setFixedWidth(130)
        appearance.addWidget(QLabel("Import palette"), 3, 0)
        appearance.addWidget(self.palette_input, 3, 1, 1, 2)
        appearance.addWidget(self.palette_apply, 3, 3)
        palette_hint = QLabel("Order: Background, Panel, Text, Accent. Coolors-style URLs are supported.")
        palette_hint.setProperty("class", "muted")
        appearance.addWidget(palette_hint, 4, 1, 1, 3)

        preview_layout = QHBoxLayout()
        preview_layout.setSpacing(5)
        self.palette_preview_labels: list[QLabel] = []
        for _ in range(4):
            swatch = QLabel()
            swatch.setFixedSize(58, 22)
            swatch.setProperty("class", "paletteSwatch")
            preview_layout.addWidget(swatch)
            self.palette_preview_labels.append(swatch)
        preview_layout.addStretch(1)
        appearance.addWidget(QLabel("Preview"), 5, 0)
        appearance.addLayout(preview_layout, 5, 1, 1, 3)

        self.custom_background = QLineEdit()
        self.custom_surface = QLineEdit()
        self.custom_text = QLineEdit()
        self.custom_accent = QLineEdit()
        self.custom_color_controls: list[tuple[QLineEdit, QPushButton]] = []
        for row, (label, field) in enumerate((
            ("Custom background", self.custom_background),
            ("Custom panel colour", self.custom_surface),
            ("Custom text colour", self.custom_text),
            ("Custom accent colour", self.custom_accent),
        ), start=6):
            field.setMaximumWidth(220)
            picker = secondary_button("Pick…")
            picker.setFixedWidth(110)
            picker.clicked.connect(
                lambda _checked=False, target=field, title=label: self.pick_custom_color(target, title)
            )
            appearance.addWidget(QLabel(label), row, 0)
            appearance.addWidget(field, row, 1)
            appearance.addWidget(picker, row, 2)
            self.custom_color_controls.append((field, picker))
        appearance.setColumnStretch(4, 1)
        root.addWidget(appearance_box)

        hotkey_box = group_box("Quick Copy Hotkey")
        hotkey_form = QFormLayout(hotkey_box)
        self.quick_copy_hotkey = QLineEdit()
        self.quick_copy_hotkey.setPlaceholderText("For example: ctrl+alt+c")
        self.quick_copy_hotkey_enabled = QCheckBox("Enable global Quick Copy hotkey")
        self.quick_copy_hotkey_state = QLabel("Hotkey is disabled.")
        self.quick_copy_hotkey_state.setProperty("class", "muted")
        hotkey_note = QLabel(
            "When enabled, this triggers the same Copy Section # action even while another app is focused."
        )
        hotkey_note.setWordWrap(True)
        hotkey_note.setProperty("class", "muted")
        hotkey_form.addRow("Shortcut", self.quick_copy_hotkey)
        hotkey_form.addRow("", self.quick_copy_hotkey_enabled)
        hotkey_form.addRow("Status", self.quick_copy_hotkey_state)
        hotkey_form.addRow("", hotkey_note)
        root.addWidget(hotkey_box)

        window_box = group_box("Startup Window")
        window_form = QFormLayout(window_box)
        self.startup_width = numeric_spin(980, 3840)
        self.startup_height = numeric_spin(660, 2160)
        apply_size = primary_button("Apply Window Size Now")
        apply_size.setMaximumWidth(240)
        apply_size.clicked.connect(
            lambda: self.apply_window_size_requested.emit(self.startup_width.value(), self.startup_height.value())
        )
        window_form.addRow("Startup width", self.startup_width)
        window_form.addRow("Startup height", self.startup_height)
        window_form.addRow("", apply_size)
        root.addWidget(window_box)

        storage_box = group_box("Saved Data")
        storage_layout = QVBoxLayout(storage_box)
        storage_note = QLabel(
            "Settings are stored in your Windows user profile. A missing or damaged file is safely replaced with defaults."
        )
        storage_note.setWordWrap(True)
        storage_note.setProperty("class", "muted")
        reset = secondary_button("Reset Everything to Defaults")
        reset.clicked.connect(self.reset_requested.emit)
        storage_layout.addWidget(storage_note)
        storage_layout.addWidget(reset, 0, Qt.AlignmentFlag.AlignLeft)
        root.addWidget(storage_box)
        root.addStretch(1)

        self.theme.currentIndexChanged.connect(lambda _index: self._appearance_edited())
        self.palette_family.currentIndexChanged.connect(self.change_palette_family)
        self.palette_preset.currentIndexChanged.connect(self.apply_selected_palette)
        self.online_palette_button.clicked.connect(self.fetch_online_palette)
        self.palette_apply.clicked.connect(self.apply_imported_palette)
        self.palette_input.returnPressed.connect(self.apply_imported_palette)
        for field in (self.custom_background, self.custom_surface, self.custom_text, self.custom_accent):
            field.textChanged.connect(lambda _text: self._appearance_edited())
        self.startup_width.valueChanged.connect(lambda _value: self.settings_changed.emit())
        self.startup_height.valueChanged.connect(lambda _value: self.settings_changed.emit())
        self.quick_copy_hotkey.editingFinished.connect(self.emit_quick_copy_hotkey)
        self.quick_copy_hotkey_enabled.toggled.connect(lambda _checked: self.emit_quick_copy_hotkey())

    def refresh_palette_presets(self) -> None:
        family = str(self.palette_family.currentData() or "All colours")
        names = PALETTE_FAMILIES.get(family, PALETTE_FAMILIES["All colours"])
        self.palette_preset.blockSignals(True)
        self.palette_preset.clear()
        self.palette_preset.addItem("Choose a built-in palette…", "")
        for name in names:
            self.palette_preset.addItem(name, name)
        self.palette_preset.blockSignals(False)

    def change_palette_family(self, _index: int) -> None:
        self.refresh_palette_presets()
        self.settings_changed.emit()

    def emit_quick_copy_hotkey(self) -> None:
        hotkey = self.quick_copy_hotkey.text().strip()
        enabled = self.quick_copy_hotkey_enabled.isChecked()
        self.quick_copy_hotkey_changed.emit(hotkey, enabled)
        self.settings_changed.emit()

    def set_quick_copy_hotkey_state(self, message: str) -> None:
        self.quick_copy_hotkey_state.setText(message)

    def collect_hotkeys(self) -> dict[str, Any]:
        return {
            "quick_copy": self.quick_copy_hotkey.text().strip() or "ctrl+alt+c",
            "quick_copy_enabled": self.quick_copy_hotkey_enabled.isChecked(),
        }

    def _appearance_edited(self) -> None:
        custom = self.theme.currentData() == "custom"
        for field, picker in self.custom_color_controls:
            field.setEnabled(custom)
            picker.setEnabled(custom)
        self.update_color_swatches()
        self.appearance_changed.emit()
        self.settings_changed.emit()

    def apply_selected_palette(self, _index: int) -> None:
        name = str(self.palette_preset.currentData() or "")
        colors = PALETTE_PRESETS.get(name)
        if colors:
            self.apply_palette(colors)

    def fetch_online_palette(self) -> None:
        self.online_palette_button.setEnabled(False)
        self.online_palette_button.setText("Fetching…")
        self.status("Fetching a random online palette…")

        def done(result: Any) -> None:
            try:
                arranged = arrange_palette_for_ui(result)
            except (TypeError, ValueError) as exc:
                failed(str(exc))
                return
            self.online_palette_button.setEnabled(True)
            self.online_palette_button.setText("Random Online Palette")
            self.palette_preset.blockSignals(True)
            self.palette_preset.setCurrentIndex(0)
            self.palette_preset.blockSignals(False)
            self.apply_palette(arranged)
            self.status("Random online palette applied.")

        def failed(message: str) -> None:
            self.online_palette_button.setEnabled(True)
            self.online_palette_button.setText("Random Online Palette")
            QMessageBox.warning(
                self,
                "Could not fetch palette",
                f"MTYH could not retrieve a palette from the online service.\n\n{message}",
            )
            self.status("Online palette request failed.")

        self.run_task(fetch_random_online_palette, done, failed)

    def apply_imported_palette(self) -> None:
        colors = parse_palette_text(self.palette_input.text())
        if len(colors) < 4:
            QMessageBox.warning(
                self,
                "Palette needs four colours",
                "Paste at least four six-digit hex colours, or a palette-generator URL containing them.",
            )
            return
        self.palette_preset.blockSignals(True)
        self.palette_preset.setCurrentIndex(0)
        self.palette_preset.blockSignals(False)
        self.apply_palette(tuple(colors[:4]))

    def apply_palette(self, colors: Sequence[str]) -> None:
        fields = (self.custom_background, self.custom_surface, self.custom_text, self.custom_accent)
        for field, color in zip(fields, colors):
            field.setText(color)
        self.theme.setCurrentIndex(max(0, self.theme.findData("custom")))
        self.palette_input.setText("  ".join(colors[:4]))
        self.update_color_swatches()
        self.settings_changed.emit()

    def pick_custom_color(self, field: QLineEdit, title: str) -> None:
        initial = QColor(field.text().strip())
        if not initial.isValid():
            initial = QColor("#ffffff")
        color = QColorDialog.getColor(
            initial,
            self,
            title,
            QColorDialog.ColorDialogOption.DontUseNativeDialog,
        )
        if color.isValid():
            field.setText(color.name())

    def update_color_swatches(self) -> None:
        for index, (field, picker) in enumerate(self.custom_color_controls):
            color = QColor(field.text().strip())
            if not color.isValid():
                picker.setStyleSheet("")
                picker.setText("Pick…")
                self.palette_preview_labels[index].setStyleSheet("")
                continue
            foreground = "#111827" if color.lightness() > 150 else "#ffffff"
            picker.setText(color.name())
            picker.setToolTip(f"Open colour picker for {color.name()}")
            picker.setStyleSheet(
                f"QPushButton {{ background: {color.name()}; color: {foreground}; font-weight: 600; }}"
            )
            self.palette_preview_labels[index].setStyleSheet(
                f"QLabel {{ background: {color.name()}; border: 1px solid rgba(127,127,127,0.55); border-radius: 5px; }}"
            )

    def load_config(self) -> None:
        appearance = self.config.get("appearance", {})
        theme = str(appearance.get("theme", "system"))
        self.theme.setCurrentIndex(max(0, self.theme.findData(theme)))
        family = str(appearance.get("palette_family", "All colours"))
        self.palette_family.setCurrentIndex(max(0, self.palette_family.findData(family)))
        self.refresh_palette_presets()
        self.custom_background.setText(str(appearance.get("custom_background", "#f4f7fb")))
        self.custom_surface.setText(str(appearance.get("custom_surface", "#ffffff")))
        self.custom_text.setText(str(appearance.get("custom_text", "#172033")))
        self.custom_accent.setText(str(appearance.get("custom_accent", "#315efb")))
        self.palette_input.setText(
            "  ".join(
                field.text()
                for field in (self.custom_background, self.custom_surface, self.custom_text, self.custom_accent)
            )
        )
        window = self.config.get("window", {})
        self.startup_width.setValue(int(window.get("width", 1280)))
        self.startup_height.setValue(int(window.get("height", 900)))
        hotkeys = self.config.get("hotkeys", {})
        self.quick_copy_hotkey.setText(str(hotkeys.get("quick_copy", "ctrl+alt+c")))
        self.quick_copy_hotkey_enabled.setChecked(bool(hotkeys.get("quick_copy_enabled", False)))
        self.set_quick_copy_hotkey_state(
            "Starting global hotkey…" if self.quick_copy_hotkey_enabled.isChecked() else "Hotkey is disabled."
        )
        self._appearance_edited()

    def collect_appearance(self) -> dict[str, str]:
        return {
            "theme": str(self.theme.currentData()),
            "palette_family": str(self.palette_family.currentData() or "All colours"),
            "custom_background": self.custom_background.text().strip() or "#f4f7fb",
            "custom_surface": self.custom_surface.text().strip() or "#ffffff",
            "custom_text": self.custom_text.text().strip() or "#172033",
            "custom_accent": self.custom_accent.text().strip() or "#315efb",
        }


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.config_store = ConfigStore()
        self.config = self.config_store.load()
        self.thread_pool = QThreadPool.globalInstance()
        self.active_workers: set[FunctionWorker] = set()
        self.quick_copy_listener: QuickCopyHotkeyThread | None = None
        self.quick_copy_hotkey_spec: tuple[str, bool] | None = None
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(450)
        self.save_timer.timeout.connect(self.save_config)
        self.setWindowTitle("MTYH")
        self.setMinimumSize(980, 660)
        window_config = self.config.get("window", {})
        screen = QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        width = min(int(window_config.get("width", 1280)), available.width())
        height = min(int(window_config.get("height", 900)), available.height())
        self.resize(max(980, width), max(660, height))
        icon = icon_path()
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self._build()
        x = window_config.get("x")
        y = window_config.get("y")
        if isinstance(x, int) and isinstance(y, int):
            candidate = QRect(x, y, self.width(), self.height())
            if any(candidate.intersects(item.availableGeometry()) for item in QGuiApplication.screens()):
                self.move(x, y)
        self._apply_style()
        hotkeys = self.config.get("hotkeys", {})
        self.configure_quick_copy_hotkey(
            str(hotkeys.get("quick_copy", "ctrl+alt+c")),
            bool(hotkeys.get("quick_copy_enabled", False)),
        )
        self.status("Ready.")

    def _build(self) -> None:
        self.setStatusBar(QStatusBar())
        toolbar = QToolBar("Main")
        toolbar.setIconSize(QSize(18, 18))
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        output_action = QAction("Open Output Folder", self)
        output_action.triggered.connect(self.open_output_folder)
        log_action = QAction("Open Logs", self)
        log_action.triggered.connect(self.open_logs_folder)
        toolbar.addAction(output_action)
        toolbar.addAction(log_action)

        central = QWidget()
        central.setObjectName("centralRoot")
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(158)
        for label in ("Text Formatter", "Image Tools", "Training Macro", "Settings"):
            item = QListWidgetItem(label)
            item.setSizeHint(QSize(140, 42))
            self.sidebar.addItem(item)
        layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self.text_page = ModernTextFormatterPage(self.config, self.status)
        self.image_page = ImageToolsPage(self.config, self.status, self.start_task)
        self.macro_page = MacroPage(self.config, self.status, self.start_task, self.save_config)
        self.settings_page = SettingsPage(self.config, self.status, self.start_task)
        self.stack.addWidget(self.text_page)
        self.stack.addWidget(self.image_page)
        self.stack.addWidget(self.macro_page)
        self.stack.addWidget(self.settings_page)
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.setCurrentRow(0)
        self.text_page.settings_changed.connect(self.schedule_save)
        self.image_page.settings_changed.connect(self.schedule_save)
        self.settings_page.settings_changed.connect(self.schedule_save)
        self.settings_page.appearance_changed.connect(self._apply_style)
        self.settings_page.reset_requested.connect(self.reset_defaults)
        self.settings_page.apply_window_size_requested.connect(self.apply_window_size)
        self.settings_page.quick_copy_hotkey_changed.connect(self.configure_quick_copy_hotkey)

    def configure_quick_copy_hotkey(self, hotkey: str, enabled: bool) -> None:
        hotkey = hotkey.strip()
        requested = (hotkey, enabled)
        if requested == self.quick_copy_hotkey_spec and self.quick_copy_listener and self.quick_copy_listener.isRunning():
            return
        if self.quick_copy_listener and self.quick_copy_listener.isRunning():
            self.quick_copy_listener.stop()
            self.quick_copy_listener.wait(1500)
        self.quick_copy_listener = None
        self.quick_copy_hotkey_spec = requested
        if not enabled:
            self.settings_page.set_quick_copy_hotkey_state("Hotkey is disabled.")
            return
        if not hotkey:
            self.settings_page.set_quick_copy_hotkey_state("Enter a shortcut before enabling the hotkey.")
            return
        listener = QuickCopyHotkeyThread(hotkey)
        listener.copy_requested.connect(self.text_page.quick_copy)
        listener.listening.connect(
            lambda key=hotkey: self.settings_page.set_quick_copy_hotkey_state(f"Global hotkey active: {key}")
        )
        listener.listener_error.connect(self.quick_copy_hotkey_error)
        self.quick_copy_listener = listener
        self.settings_page.set_quick_copy_hotkey_state(f"Starting global hotkey: {hotkey}…")
        listener.start()

    def quick_copy_hotkey_error(self, message: str) -> None:
        self.settings_page.set_quick_copy_hotkey_state(message)
        self.status(message)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #f5f7fa;
                color: #111827;
                font-family: "Segoe UI";
                font-size: 10pt;
            }
            QListWidget#sidebar {
                background: #111827;
                color: #d1d5db;
                border: none;
                padding: 10px 8px;
                outline: none;
            }
            QListWidget#sidebar::item {
                border-radius: 6px;
                padding: 10px;
                margin: 2px 0;
                outline: none;
            }
            QListWidget#sidebar::item:selected {
                background: #2563eb;
                color: #ffffff;
            }
            QListWidget#sidebar::item:hover {
                background: #1f2937;
                color: #ffffff;
            }
            QListWidget#sidebar:focus, QListWidget#sidebar::item:focus {
                outline: none;
            }
            QToolBar {
                background: #ffffff;
                border-bottom: 1px solid #d8dee8;
                padding: 4px;
                spacing: 6px;
            }
            QGroupBox {
                background: #ffffff;
                border: 1px solid #d8dee8;
                border-radius: 6px;
                margin-top: 12px;
                padding: 10px;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QListWidget, QTableWidget {
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                padding: 4px;
                selection-background-color: #bfdbfe;
            }
            QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
                border: 1px solid #2563eb;
            }
            QPushButton {
                background: #e5e7eb;
                border: 1px solid #cbd5e1;
                border-radius: 5px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background: #dbeafe;
            }
            QPushButton:pressed {
                background: #bfdbfe;
            }
            QPushButton:disabled {
                color: #9ca3af;
                background: #f3f4f6;
            }
            QPushButton[role="primary"] {
                background: #2563eb;
                border: 1px solid #1d4ed8;
                color: #ffffff;
                font-weight: 600;
            }
            QPushButton[role="primary"]:hover {
                background: #1d4ed8;
            }
            QLabel[class="sectionLabel"] {
                font-weight: 600;
                padding-bottom: 4px;
            }
            QLabel[class="muted"] {
                color: #4b5563;
            }
            QTabWidget::pane {
                border: none;
            }
            QTabBar::tab {
                background: #e5e7eb;
                padding: 8px 14px;
                border-top-left-radius: 5px;
                border-top-right-radius: 5px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background: #ffffff;
                color: #111827;
            }
            QStatusBar {
                background: #ffffff;
                border-top: 1px solid #d8dee8;
            }
            """
        )

    def _apply_style(self) -> None:
        appearance = (
            self.settings_page.collect_appearance()
            if hasattr(self, "settings_page")
            else self.config.get("appearance", {})
        )
        requested = str(appearance.get("theme", "system"))
        if requested == "system":
            system_background = QApplication.palette().window().color()
            requested = "dark" if system_background.lightness() < 128 else "light"

        if requested == "dark":
            colors = {
                "background": "#0f172a",
                "surface": "#172033",
                "input": "#111827",
                "sidebar": "#0b1220",
                "text": "#f1f5f9",
                "muted": "#a8b3c7",
                "border": "#334155",
                "accent": "#5b7cfa",
                "hover": "#26334a",
                "selection": "#314b86",
            }
        elif requested == "custom":
            def valid_color(key: str, fallback: str) -> str:
                value = str(appearance.get(key, fallback))
                return value if QColor(value).isValid() else fallback

            background = valid_color("custom_background", "#f4f7fb")
            surface = valid_color("custom_surface", "#ffffff")
            text_color = valid_color("custom_text", "#172033")
            accent = valid_color("custom_accent", "#315efb")
            colors = {
                "background": background,
                "surface": surface,
                "input": surface,
                "sidebar": QColor(background).darker(180).name(),
                "text": text_color,
                "muted": QColor(text_color).lighter(145).name(),
                "border": QColor(surface).darker(125).name(),
                "accent": accent,
                "hover": QColor(surface).darker(108).name(),
                "selection": QColor(accent).lighter(160).name(),
            }
        else:
            colors = {
                "background": "#f4f7fb",
                "surface": "#ffffff",
                "input": "#ffffff",
                "sidebar": "#101828",
                "text": "#172033",
                "muted": "#667085",
                "border": "#d0d5dd",
                "accent": "#315efb",
                "hover": "#eef2ff",
                "selection": "#dbe5ff",
            }

        colors["input_text"] = readable_text_color(colors["text"], colors["input"])
        colors["dialog_text"] = readable_text_color(colors["text"], colors["surface"])
        colors["placeholder"] = readable_text_color(colors["muted"], colors["input"], minimum_ratio=3.0)
        colors["selection_text"] = readable_text_color("#ffffff", colors["accent"])
        colors["hover_text"] = readable_text_color(colors["text"], colors["hover"])

        self.setStyleSheet(
            f"""
            QMainWindow, QDialog {{
                background: {colors['background']};
            }}
            QWidget {{
                background: transparent;
                color: {colors['text']};
                font-family: "Segoe UI";
                font-size: 10pt;
            }}
            QWidget#centralRoot {{ background: {colors['background']}; }}
            QMessageBox {{ background: {colors['surface']}; }}
            QMessageBox QLabel {{
                background: transparent; color: {colors['dialog_text']};
            }}
            QMessageBox QPushButton {{
                background: {colors['hover']}; color: {colors['dialog_text']};
                border: 1px solid {colors['border']}; min-width: 78px;
            }}
            QMessageBox QPushButton:hover {{
                background: {colors['accent']}; color: {colors['selection_text']};
            }}
            QLabel[class="pageTitle"] {{ font-size: 18pt; font-weight: 700; }}
            QLabel[class="sectionLabel"], QLabel[class="queueProgress"] {{ font-weight: 650; }}
            QLabel[class="muted"] {{ color: {colors['muted']}; }}
            QListWidget#sidebar {{
                background: {colors['sidebar']}; color: #dbe3f0; border: none; padding: 12px 8px; outline: none;
            }}
            QListWidget#sidebar::item {{ border-radius: 8px; padding: 10px; margin: 3px 0; outline: none; }}
            QListWidget#sidebar::item:selected {{ background: {colors['accent']}; color: #ffffff; }}
            QListWidget#sidebar::item:hover {{ background: #29364c; color: #ffffff; }}
            QListWidget#sidebar:focus, QListWidget#sidebar::item:focus {{ outline: none; }}
            QToolBar, QStatusBar {{
                background: {colors['surface']}; border: none; border-bottom: 1px solid {colors['border']};
                padding: 5px; spacing: 8px;
            }}
            QStatusBar {{ border-top: 1px solid {colors['border']}; border-bottom: none; }}
            QGroupBox {{
                background: {colors['surface']}; border: 1px solid {colors['border']};
                border-radius: 10px; margin-top: 12px; padding: 10px; font-weight: 650;
            }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 5px; }}
            QGroupBox#appearanceBox {{ background: transparent; border: none; padding: 8px 0 0 0; }}
            QGroupBox#appearanceBox::title {{ left: 0; padding: 0; }}
            QFrame#quickCopyBar {{
                background: {colors['surface']}; border: 1px solid {colors['border']}; border-radius: 10px;
            }}
            QProgressBar#copyTimeline {{
                background: {colors['hover']}; border: none; border-radius: 5px; min-height: 9px; max-height: 9px;
            }}
            QProgressBar#copyTimeline::chunk {{
                background: {colors['accent']}; border-radius: 5px;
            }}
            QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QListWidget, QTableWidget {{
                background: {colors['input']}; color: {colors['input_text']}; border: 1px solid {colors['border']};
                border-radius: 7px; padding: 6px; selection-background-color: {colors['selection']};
                selection-color: {colors['input_text']}; placeholder-text-color: {colors['placeholder']};
            }}
            QComboBox QAbstractItemView, QMenu {{
                background: {colors['input']}; color: {colors['input_text']};
                border: 1px solid {colors['border']}; outline: none;
                selection-background-color: {colors['accent']}; selection-color: {colors['selection_text']};
            }}
            QComboBox QAbstractItemView::item {{ min-height: 28px; padding: 5px 8px; }}
            QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected {{
                background: {colors['accent']}; color: {colors['selection_text']};
            }}
            QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
            QComboBox:focus, QTableWidget:focus {{ border: 1px solid {colors['accent']}; }}
            QHeaderView::section {{
                background: {colors['hover']}; color: {colors['hover_text']}; border: none;
                border-right: 1px solid {colors['border']}; border-bottom: 1px solid {colors['border']};
                padding: 8px; font-weight: 650;
            }}
            QHeaderView::section:hover {{ background: {colors['selection']}; }}
            QTableWidget {{ gridline-color: {colors['border']}; alternate-background-color: {colors['hover']}; }}
            QPushButton {{
                background: {colors['hover']}; color: {colors['text']}; border: 1px solid {colors['border']};
                border-radius: 7px; padding: 7px 13px;
            }}
            QPushButton:hover {{ border-color: {colors['accent']}; }}
            QPushButton:pressed {{ background: {colors['selection']}; }}
            QPushButton:disabled {{ color: {colors['muted']}; border-color: {colors['border']}; }}
            QPushButton[role="primary"] {{
                background: {colors['accent']}; border-color: {colors['accent']}; color: #ffffff; font-weight: 650;
            }}
            QPushButton[role="primary"]:hover {{ background: {QColor(colors['accent']).darker(112).name()}; }}
            QTabWidget::pane {{ border: 1px solid {colors['border']}; border-radius: 8px; background: {colors['surface']}; }}
            QTabBar::tab {{
                background: {colors['hover']}; color: {colors['muted']}; padding: 9px 16px;
                border-top-left-radius: 7px; border-top-right-radius: 7px; margin-right: 3px;
            }}
            QTabBar::tab:selected {{ background: {colors['surface']}; color: {colors['text']}; font-weight: 650; }}
            QSplitter::handle {{ background: transparent; width: 5px; height: 5px; }}
            QScrollBar:vertical {{ background: {colors['background']}; width: 10px; margin: 0; }}
            QScrollBar::handle:vertical {{ background: {colors['border']}; min-height: 24px; border-radius: 5px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            """
        )

    def status(self, message: str) -> None:
        self.statusBar().showMessage(message, 6000)

    def start_task(
        self,
        function: Callable[..., Any],
        on_result: Callable[[Any], None],
        on_error: Callable[[str], None],
        *args: Any,
    ) -> None:
        worker = FunctionWorker(function, *args)
        self.active_workers.add(worker)
        worker.signals.result.connect(on_result)
        worker.signals.error.connect(on_error)

        def release_worker() -> None:
            self.active_workers.discard(worker)
            LOGGER.info("Background task finished; %d task(s) remain", len(self.active_workers))

        worker.signals.finished.connect(release_worker)
        self.thread_pool.start(worker)

    def schedule_save(self, *_: Any) -> None:
        self.save_timer.start()

    def save_config(self) -> None:
        self.config["formatter"] = self.text_page.collect_config()
        try:
            self.config["image"] = self.image_page.collect_config()
        except ValueError:
            LOGGER.warning("Image settings contain an incomplete colour; postponing that part of the save")
        self.config["macro"] = self.macro_page.collect_config()
        self.config["hotkeys"] = self.settings_page.collect_hotkeys()
        self.config["appearance"] = self.settings_page.collect_appearance()
        self.config["window"] = {
            "width": self.settings_page.startup_width.value(),
            "height": self.settings_page.startup_height.value(),
            "x": self.x(),
            "y": self.y(),
        }
        selected_output = self.image_page.stitch_tab.output_dir.text().strip()
        if selected_output:
            self.config["output_dir"] = selected_output
        output_dir = str(self.config.get("output_dir", default_output_dir()))
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        try:
            self.config_store.save(self.config)
        except OSError as exc:
            LOGGER.exception("Could not save settings")
            self.status(f"Could not save settings: {exc}")

    def apply_window_size(self, width: int, height: int) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen else QRect(0, 0, width, height)
        self.resize(min(width, available.width()), min(height, available.height()))
        self.schedule_save()
        self.status(f"Window size set to {self.width()} × {self.height()}.")

    def reset_defaults(self) -> None:
        answer = QMessageBox.question(
            self,
            "Reset MTYH settings",
            "Reset line rules, character mappings, image tools, theme, and startup size to defaults?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.config.clear()
        self.config.update(default_config())
        self.text_page.load_config()
        self.image_page.load_config()
        self.macro_page._load_config()
        self.settings_page.load_config()
        hotkeys = self.settings_page.collect_hotkeys()
        self.configure_quick_copy_hotkey(
            str(hotkeys.get("quick_copy", "ctrl+alt+c")),
            bool(hotkeys.get("quick_copy_enabled", False)),
        )
        self._apply_style()
        self.apply_window_size(1280, 900)
        self.save_config()
        self.status("All settings were reset to defaults.")

    def open_output_folder(self) -> None:
        folder = Path(self.config.get("output_dir", default_output_dir()))
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def open_logs_folder(self) -> None:
        folder = logs_dir()
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def closeEvent(self, event: Any) -> None:
        self.macro_page.shutdown()
        if self.quick_copy_listener and self.quick_copy_listener.isRunning():
            self.quick_copy_listener.stop()
            self.quick_copy_listener.wait(1500)
        self.settings_page.startup_width.blockSignals(True)
        self.settings_page.startup_height.blockSignals(True)
        self.settings_page.startup_width.setValue(self.width())
        self.settings_page.startup_height.setValue(self.height())
        self.settings_page.startup_width.blockSignals(False)
        self.settings_page.startup_height.blockSignals(False)
        self.save_config()
        if self.active_workers:
            self.thread_pool.waitForDone(3000)
        super().closeEvent(event)
