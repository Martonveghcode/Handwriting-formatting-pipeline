from pathlib import Path

from mtyh.ui import main_window


def test_sidebar_focus_outline_is_disabled() -> None:
    stylesheet_source = Path(main_window.__file__).read_text(encoding="utf-8")
    focus_rule = "QListWidget#sidebar:focus, QListWidget#sidebar::item:focus"

    assert stylesheet_source.count(focus_rule) == 2
    assert "outline: none;" in stylesheet_source
