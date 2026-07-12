from pathlib import Path

from PIL import Image

from mtyh.logic.image_tools import (
    ExportMode,
    ImageProcessingSettings,
    cm_to_px,
    detect_and_connect_image,
    process_single_image,
    remove_yellow_pixels,
    stitch_images,
)


def test_cm_to_px() -> None:
    assert cm_to_px(2.54, dpi=100) == 100


def test_stitch_images_tracks_segment_bounds() -> None:
    first = Image.new("RGBA", (10, 5), (255, 255, 255, 255))
    second = Image.new("RGBA", (8, 4), (255, 255, 255, 255))
    stitched, bounds = stitch_images([first, second], overlap_px=2)
    assert stitched.size == (10, 7)
    assert bounds == [(0, 5), (3, 7)]


def test_remove_yellow_pixels_replaces_guide_colors() -> None:
    image = Image.new("RGB", (3, 3), "white")
    image.putpixel((1, 1), (241, 226, 0))
    cleaned = remove_yellow_pixels(image)
    assert cleaned.getpixel((1, 1)) == (255, 255, 255)


def test_detect_and_connect_image_draws_between_yellow_segments() -> None:
    image = Image.new("RGB", (20, 10), "white")
    for x in range(1, 4):
        image.putpixel((x, 5), (241, 226, 0))
    for x in range(10, 13):
        image.putpixel((x, 5), (241, 226, 0))

    result = detect_and_connect_image(image, line_thickness=1, y_tolerance=1, line_color=(0, 0, 0))
    assert result.getpixel((15, 5)) == (0, 0, 0)


def test_thin_connected_line_has_no_pixel_gaps() -> None:
    image = Image.new("RGB", (60, 20), "white")
    for start in (2, 24):
        for y in range(9, 12):
            for x in range(start, start + 4):
                image.putpixel((x, y), (241, 226, 0))
    result = detect_and_connect_image(image, line_thickness=1, y_tolerance=2, line_color=(0, 0, 0))
    assert all(result.getpixel((x, 10))[:3] == (0, 0, 0) for x in range(2, 60))


def test_process_single_image_writes_png(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    output = tmp_path / "result.png"
    Image.new("RGB", (10, 10), "white").save(source)

    result_path = process_single_image(
        source,
        output,
        ImageProcessingSettings(connect_lines=False, remove_yellow=True),
    )
    assert result_path == output
    assert output.exists()


def test_process_single_image_can_repeat_in_one_session(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (20, 20), "white").save(source)
    settings = ImageProcessingSettings(connect_lines=False, remove_yellow=True)
    for index in range(4):
        output = tmp_path / f"result_{index}.png"
        process_single_image(source, output, settings)
        with Image.open(output) as result:
            result.verify()


def test_export_mode_values_are_stable() -> None:
    assert ExportMode.PDF.value == "pdf"
