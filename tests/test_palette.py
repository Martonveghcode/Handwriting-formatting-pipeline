from mtyh.ui.main_window import (
    PALETTE_FAMILIES,
    PALETTE_PRESETS,
    arrange_palette_for_ui,
    color_contrast_ratio,
    fetch_random_online_palette,
    parse_palette_text,
    readable_text_color,
)


def test_colour_families_only_reference_available_palettes() -> None:
    assert {"Blue", "Green", "Purple", "Warm", "Neutral"} <= set(PALETTE_FAMILIES)
    for names in PALETTE_FAMILIES.values():
        assert names
        assert set(names) <= set(PALETTE_PRESETS)


def test_parse_palette_from_generator_url() -> None:
    url = "https://coolors.co/0c1821-1d2d44-f0ebd8-ccc9dc"
    assert parse_palette_text(url) == ["#0c1821", "#1d2d44", "#f0ebd8", "#ccc9dc"]


def test_parse_palette_from_pasted_hex_values() -> None:
    assert parse_palette_text("#112233, 445566 | #AABBCC #dDeEfF") == [
        "#112233",
        "#445566",
        "#aabbcc",
        "#ddeeff",
    ]


def test_readable_text_color_replaces_low_contrast_palette_text() -> None:
    selected = readable_text_color("#f0ebd8", "#f3f3f3")
    assert selected == "#000000"
    assert color_contrast_ratio(selected, "#f3f3f3") >= 4.5


def test_readable_text_color_preserves_accessible_choice() -> None:
    assert readable_text_color("#f0ebd8", "#1d2d44") == "#f0ebd8"


def test_online_palette_is_arranged_into_readable_ui_roles() -> None:
    background, panel, text, accent = arrange_palette_for_ui(
        ["#de7748", "#47bf6d", "#8758eb", "#b65f37", "#f6f1df"]
    )
    assert color_contrast_ratio(text, panel) >= 4.5
    assert background != panel
    assert accent not in {background, panel, text}


def test_fetch_online_palette_validates_json(monkeypatch) -> None:
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"colors":["#112233","#445566","#778899","#aabbcc","#ddeeff"]}'

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: FakeResponse())
    assert fetch_random_online_palette() == ["#112233", "#445566", "#778899", "#aabbcc", "#ddeeff"]
