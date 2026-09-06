from __future__ import annotations

import io
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

LOGGER = logging.getLogger(__name__)


class ExportMode(str, Enum):
    STITCHED_PNG = "stitched_png"
    A4_PNG = "a4_png"
    PDF = "pdf"


@dataclass(frozen=True)
class ImageProcessingSettings:
    line_thickness: int = 7
    y_tolerance: int = 2
    line_color: tuple[int, int, int] = (0, 0, 0)
    overlap_px: int = 0
    connect_lines: bool = True
    remove_yellow: bool = True
    dpi: int = 300


def cm_to_px(cm: float, dpi: int = 300) -> int:
    return int((cm / 2.54) * dpi)


def flatten_transparency(
    image: Image.Image,
    background_color: tuple[int, int, int] = (255, 255, 255),
) -> Image.Image:
    if image.mode == "RGBA":
        flattened = Image.new("RGB", image.size, background_color)
        flattened.paste(image, mask=image.split()[3])
        return flattened
    return image.convert("RGB")


def _saturated_yellow_mask(pixels: np.ndarray) -> np.ndarray:
    return (
        ((pixels[:, :, 0] >= 200) & (pixels[:, :, 0] <= 255))
        & ((pixels[:, :, 1] >= 180) & (pixels[:, :, 1] <= 240))
        & (pixels[:, :, 2] < 50)
    )


def _removal_yellow_mask(pixels: np.ndarray) -> np.ndarray:
    saturated = _saturated_yellow_mask(pixels)
    pale = (
        (pixels[:, :, 0] >= 220)
        & (pixels[:, :, 1] >= 200)
        & (pixels[:, :, 2] >= 80)
        & (pixels[:, :, 2] <= 220)
        & (pixels[:, :, 0] >= pixels[:, :, 1])
        & (pixels[:, :, 1] > pixels[:, :, 2])
    )
    return saturated | pale


def remove_yellow_pixels(
    image: Image.Image,
    replacement_color: tuple[int, int, int] = (255, 255, 255),
) -> Image.Image:
    base = flatten_transparency(image).convert("RGB")
    try:
        pixels = np.array(base)
        mask = _removal_yellow_mask(pixels)
        pixels[mask] = replacement_color
        return Image.fromarray(pixels, mode="RGB")
    finally:
        base.close()


def _cluster_centers_from_mask(yellow_mask: np.ndarray, radius: int = 6) -> list[tuple[int, int]]:
    """Find stroke centers using the original generator's six-pixel clustering radius.

    Pillow performs the dilation in native code, then a run-based union-find labels
    connected components. This matches the proven DBSCAN behavior closely without
    adding scikit-learn (and its large compiled runtime) to the executable.
    """
    if not bool(yellow_mask.any()):
        return []

    # Expanding each side by half the DBSCAN radius joins points whose total
    # separation is at most ``radius`` without merging nearby distinct strokes.
    half_radius = max(1, (int(radius) - 1) // 2)
    size = (half_radius * 2) + 1
    expanded = np.asarray(
        Image.fromarray((yellow_mask.astype(np.uint8) * 255), mode="L").filter(
            ImageFilter.MaxFilter(size)
        )
    ) > 0

    parent: list[int] = []

    def make_label() -> int:
        label = len(parent)
        parent.append(label)
        return label

    def find(label: int) -> int:
        while parent[label] != label:
            parent[label] = parent[parent[label]]
            label = parent[label]
        return label

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    rows: list[list[tuple[int, int, int]]] = []
    previous: list[tuple[int, int, int]] = []
    for row in expanded:
        xs = np.flatnonzero(row)
        current: list[tuple[int, int, int]] = []
        if xs.size:
            split_at = np.where(np.diff(xs) > 1)[0] + 1
            for run in np.split(xs, split_at):
                start, end = int(run[0]), int(run[-1])
                overlapping = [item for item in previous if item[0] <= end and item[1] >= start]
                label = overlapping[0][2] if overlapping else make_label()
                for _, _, other_label in overlapping[1:]:
                    union(label, other_label)
                current.append((start, end, label))
        rows.append(current)
        previous = current

    accumulators: dict[int, list[int]] = {}
    for y, row_runs in enumerate(rows):
        yellow_xs = np.flatnonzero(yellow_mask[y])
        run_index = 0
        for x in yellow_xs:
            while run_index + 1 < len(row_runs) and x > row_runs[run_index][1]:
                run_index += 1
            if not row_runs:
                break
            root = find(row_runs[run_index][2])
            totals = accumulators.setdefault(root, [0, 0, 0])
            totals[0] += int(x)
            totals[1] += y
            totals[2] += 1

    return [
        (int(total_x / count), int(total_y / count))
        for total_x, total_y, count in accumulators.values()
        if count >= 3
    ]


def _cluster_rows_by_tolerance(
    centers: Sequence[tuple[int, int]],
    tolerance: int,
) -> list[list[tuple[int, int]]]:
    if not centers:
        return []
    eps = max(1, int(abs(tolerance)))
    sorted_centers = sorted(centers, key=lambda point: point[1])
    rows: list[list[tuple[int, int]]] = []
    row_anchors: list[int] = []

    for x, y in sorted_centers:
        if not rows:
            rows.append([(x, y)])
            row_anchors.append(y)
            continue

        if abs(y - rows[-1][-1][1]) <= eps:
            rows[-1].append((x, y))
            row_anchors[-1] = int(np.median([point[1] for point in rows[-1]]))
        else:
            rows.append([(x, y)])
            row_anchors.append(y)

    return rows


def detect_and_connect_image(
    image: Image.Image,
    line_thickness: int,
    y_tolerance: int,
    line_color: tuple[int, int, int],
) -> Image.Image:
    base = flatten_transparency(image).convert("RGB")
    pixels = np.array(base)
    yellow_mask = _saturated_yellow_mask(pixels)

    centers = _cluster_centers_from_mask(yellow_mask)
    if not centers:
        return base

    rows = _cluster_rows_by_tolerance(centers, y_tolerance)
    thickness = max(1, int(line_thickness))
    color = tuple(max(0, min(255, int(value))) for value in line_color)
    draw_layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(draw_layer)
    try:
        for row_points in rows:
            if len(row_points) < 2:
                continue
            ys = [point[1] for point in row_points]
            xs = [point[0] for point in row_points]
            line_y = int(np.median(ys))
            x_start = max(0, int(min(xs)) - thickness)
            x_end = base.width - 1
            draw.line(
                [(x_start, line_y), (x_end, line_y)],
                fill=color + (255,),
                width=thickness,
            )
        rgba_base = base.convert("RGBA")
        try:
            composite = Image.alpha_composite(rgba_base, draw_layer)
            try:
                return composite.convert("RGB")
            finally:
                composite.close()
        finally:
            rgba_base.close()
    finally:
        draw_layer.close()
        base.close()


def resize_to_match_width(
    images: Sequence[Image.Image],
    target_width: int,
) -> list[Image.Image]:
    resized_images: list[Image.Image] = []
    for image in images:
        if image.width == target_width:
            resized_images.append(image)
            continue
        padded = Image.new("RGBA", (target_width, image.height), (255, 255, 255, 0))
        padded.paste(image, (0, 0))
        resized_images.append(padded)
    return resized_images


def stitch_images(
    images: Sequence[Image.Image],
    overlap_px: int = 0,
) -> tuple[Image.Image, list[tuple[int, int]]]:
    if not images:
        raise ValueError("No images were supplied.")

    rgba_images = [image.convert("RGBA") for image in images]
    resized_images: list[Image.Image] = []
    try:
        base_width = max(image.width for image in rgba_images)
        resized_images = resize_to_match_width(rgba_images, base_width)
        overlap_value = int(max(0, overlap_px))

        y_offset = 0
        segments: list[tuple[int, int, Image.Image]] = []
        for index, image in enumerate(resized_images):
            segment_start = y_offset
            segment_end = segment_start + image.height
            segments.append((segment_start, segment_end, image))
            y_offset = segment_end
            if index < len(resized_images) - 1:
                effective_overlap = min(overlap_value, max(0, image.height - 1))
                y_offset = max(0, y_offset - effective_overlap)

        total_height = max(max(end for _, end, _ in segments), 1)
        stitched = Image.new("RGBA", (base_width, total_height))
        bounds: list[tuple[int, int]] = []
        for start, end, segment in segments:
            stitched.paste(segment, (0, start))
            bounds.append((start, end))
        return stitched, bounds
    finally:
        for image in {id(item): item for item in [*rgba_images, *resized_images]}.values():
            image.close()


def prepare_printable_a4(image: Image.Image, dpi: int = 300) -> Image.Image:
    a4_width_px = cm_to_px(21, dpi)
    a4_height_px = cm_to_px(29.7, dpi)
    margin_left = cm_to_px(0.4, dpi)
    margin_right = cm_to_px(0.5, dpi)
    margin_top = cm_to_px(2.0, dpi)

    printable_width = a4_width_px - margin_left - margin_right
    available_height = a4_height_px - margin_top

    output = flatten_transparency(image)
    img_width, img_height = output.size

    try:
        if img_width > printable_width:
            scale_factor = printable_width / img_width
            resized = output.resize(
                (printable_width, int(round(img_height * scale_factor))),
                Image.Resampling.LANCZOS,
            )
            output.close()
            output = resized
            img_width, img_height = output.size

        if img_height > available_height:
            raise ValueError("Image is too tall for a single A4 page with current margins.")

        canvas = Image.new("RGB", (a4_width_px, a4_height_px), "white")
        canvas.paste(output, (margin_left, margin_top))
        return canvas
    finally:
        output.close()


def generate_pdf_pages(
    image: Image.Image,
    segments: Sequence[tuple[int, int]],
    dpi: int = 300,
) -> list[Image.Image]:
    a4_width_px = cm_to_px(21, dpi)
    a4_height_px = cm_to_px(29.7, dpi)
    margin_left = cm_to_px(0.4, dpi)
    margin_right = cm_to_px(0.5, dpi)
    margin_top = cm_to_px(2.0, dpi)

    printable_width = a4_width_px - margin_left - margin_right
    printable_height = a4_height_px - margin_top

    output = flatten_transparency(image)
    img_width, img_height = output.size
    if not segments:
        raise ValueError("No segment data available for pagination.")

    if img_width > printable_width:
        scale_factor = printable_width / img_width
        resized = output.resize(
            (printable_width, int(round(img_height * scale_factor))),
            Image.Resampling.LANCZOS,
        )
        output.close()
        output = resized
        segments = [
            (int(round(start * scale_factor)), int(round(end * scale_factor)))
            for start, end in segments
        ]
        img_width, img_height = output.size

    normalized_segments: list[tuple[int, int]] = []
    for start, end in segments:
        start = max(0, min(int(start), img_height))
        end = max(0, min(int(end), img_height))
        if end > start:
            normalized_segments.append((start, end))
    if not normalized_segments:
        output.close()
        return []

    pages_meta: list[tuple[int, int, list[tuple[int, int]]]] = []
    page_start = None
    page_end = None
    current_segments: list[tuple[int, int]] = []

    for start, end in normalized_segments:
        if end - start > printable_height:
            raise ValueError("A source image exceeds printable height for a page.")
        if page_start is None:
            page_start = start
            page_end = end
            current_segments = [(start, end)]
            continue

        proposed_end = max(page_end, end)
        if proposed_end - page_start <= printable_height:
            page_end = proposed_end
            current_segments.append((start, end))
        else:
            pages_meta.append((page_start, page_end, current_segments))
            page_start = start
            page_end = end
            current_segments = [(start, end)]

    if current_segments:
        pages_meta.append((page_start, page_end, current_segments))

    pages: list[Image.Image] = []
    try:
        for this_page_start, _, page_segments in pages_meta:
            canvas = Image.new("RGB", (a4_width_px, a4_height_px), "white")
            for start, end in page_segments:
                chunk = output.crop((0, start, img_width, end))
                try:
                    offset_y = margin_top + (start - this_page_start)
                    canvas.paste(chunk, (margin_left, int(offset_y)))
                finally:
                    chunk.close()
            pages.append(canvas)
        return pages
    finally:
        output.close()


def image_to_png_bytes(image: Image.Image, dpi: int | None = None) -> bytes:
    buffer = io.BytesIO()
    kwargs: dict[str, object] = {}
    if dpi:
        kwargs["dpi"] = (dpi, dpi)
    image.save(buffer, format="PNG", **kwargs)
    return buffer.getvalue()


def pages_to_pdf_bytes(pages: Sequence[Image.Image], dpi: int = 300) -> bytes:
    if not pages:
        raise ValueError("No pages available for PDF export.")
    buffer = io.BytesIO()
    first_page, *remaining_pages = pages
    first_page.save(
        buffer,
        format="PDF",
        resolution=float(dpi),
        save_all=True,
        append_images=remaining_pages,
    )
    return buffer.getvalue()


def _prepare_source_for_stitch(
    file_path: Path | str,
    target_width: int,
    settings: ImageProcessingSettings,
) -> Image.Image:
    """Load and process one source while bounding peak memory to one image."""
    with Image.open(file_path) as source:
        source.load()
        working = source.convert("RGBA")
    if working.width != target_width:
        padded = Image.new("RGBA", (target_width, working.height), (255, 255, 255, 0))
        padded.paste(working, (0, 0))
        working.close()
        working = padded
    if settings.connect_lines:
        connected = detect_and_connect_image(
            working,
            settings.line_thickness,
            settings.y_tolerance,
            settings.line_color,
        )
        working.close()
        working = connected
    if settings.remove_yellow:
        cleaned = remove_yellow_pixels(working)
        working.close()
        working = cleaned
    else:
        flattened = flatten_transparency(working)
        working.close()
        working = flattened
    return working


def write_paginated_pdf_streaming(
    file_paths: Sequence[Path | str],
    output_path: Path | str,
    settings: ImageProcessingSettings,
    title: str | None = None,
) -> None:
    """Write the zero-overlap PDF path without allocating one giant stitched image.

    Page geometry and source ordering match ``stitch_images`` plus
    ``generate_pdf_pages``. Guide processing uses the same functions as before,
    applied to one padded source at a time.
    """
    if not file_paths:
        raise ValueError("No images were supplied.")
    output_path = Path(output_path)
    source_meta: list[tuple[Path | str, int, int]] = []
    for file_path in file_paths:
        with Image.open(file_path) as image:
            source_meta.append((file_path, image.width, image.height))

    base_width = max(width for _, width, _ in source_meta)
    a4_width_px = cm_to_px(21, settings.dpi)
    a4_height_px = cm_to_px(29.7, settings.dpi)
    margin_left = cm_to_px(0.4, settings.dpi)
    margin_right = cm_to_px(0.5, settings.dpi)
    margin_top = cm_to_px(2.0, settings.dpi)
    printable_width = a4_width_px - margin_left - margin_right
    printable_height = a4_height_px - margin_top
    scale_factor = min(1.0, printable_width / base_width)
    rendered_width = printable_width if scale_factor < 1.0 else base_width

    scaled_segments: list[tuple[Path | str, int, int]] = []
    y_offset = 0
    for file_path, _, height in source_meta:
        start = int(round(y_offset * scale_factor))
        y_offset += height
        end = int(round(y_offset * scale_factor))
        if end - start > printable_height:
            raise ValueError("A source image exceeds printable height for a page.")
        scaled_segments.append((file_path, start, end))

    page_groups: list[list[tuple[Path | str, int, int]]] = []
    current: list[tuple[Path | str, int, int]] = []
    page_start = 0
    for segment in scaled_segments:
        _, start, end = segment
        if not current:
            current = [segment]
            page_start = start
        elif end - page_start <= printable_height:
            current.append(segment)
        else:
            page_groups.append(current)
            current = [segment]
            page_start = start
    if current:
        page_groups.append(current)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mtyh_pdf_", dir=output_path.parent) as temp_dir:
        temp_root = Path(temp_dir)
        page_paths: list[Path] = []
        for page_index, group in enumerate(page_groups, start=1):
            canvas = Image.new("RGB", (a4_width_px, a4_height_px), "white")
            group_start = group[0][1]
            try:
                for file_path, start, end in group:
                    source = _prepare_source_for_stitch(file_path, base_width, settings)
                    try:
                        target_height = end - start
                        if source.size != (rendered_width, target_height):
                            resized = source.resize(
                                (rendered_width, target_height),
                                Image.Resampling.LANCZOS,
                            )
                            source.close()
                            source = resized
                        canvas.paste(source, (margin_left, margin_top + (start - group_start)))
                    finally:
                        source.close()
                page_path = temp_root / f"page_{page_index:05d}.png"
                canvas.save(
                    page_path,
                    format="PNG",
                    dpi=(settings.dpi, settings.dpi),
                    compress_level=1,
                )
                page_paths.append(page_path)
            finally:
                canvas.close()

        opened_pages = [Image.open(path) for path in page_paths]
        try:
            first_page, *remaining_pages = opened_pages
            first_page.save(
                output_path,
                format="PDF",
                resolution=float(settings.dpi),
                save_all=True,
                append_images=remaining_pages,
                title=title or "Handwriting",
            )
        finally:
            for page in opened_pages:
                page.close()


def load_images(paths: Sequence[Path | str]) -> list[Image.Image]:
    images: list[Image.Image] = []
    for path in paths:
        with Image.open(path) as image:
            images.append(image.convert("RGBA"))
    return images


def process_single_image(
    input_path: Path | str,
    output_path: Path | str,
    settings: ImageProcessingSettings,
) -> Path:
    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.stem}.{uuid.uuid4().hex}.tmp")
    result: Image.Image | None = None
    try:
        with Image.open(input_path) as image:
            image.load()
            result = image.convert("RGBA")
        if settings.connect_lines:
            connected = detect_and_connect_image(
                result,
                settings.line_thickness,
                settings.y_tolerance,
                settings.line_color,
            )
            result.close()
            result = connected
        if settings.remove_yellow:
            cleaned = remove_yellow_pixels(result)
            result.close()
            result = cleaned
        else:
            flattened = flatten_transparency(result)
            result.close()
            result = flattened

        result.save(temporary, format="PNG", dpi=(settings.dpi, settings.dpi))
        os.replace(temporary, output_path)
        LOGGER.info("Saved processed image to %s", output_path)
        return output_path
    except Exception:
        LOGGER.exception("Single-image processing failed for %s", input_path)
        raise
    finally:
        if result is not None:
            result.close()
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            LOGGER.warning("Could not clean temporary file %s", temporary, exc_info=True)


def process_stitch_export(
    file_paths: Sequence[Path | str],
    output_path: Path | str,
    export_mode: ExportMode,
    settings: ImageProcessingSettings,
    delete_originals: bool = False,
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.stem}.{uuid.uuid4().hex}.tmp")
    images: list[Image.Image] = []
    result: Image.Image | None = None
    pages: list[Image.Image] = []
    try:
        if export_mode == ExportMode.PDF and int(max(0, settings.overlap_px)) == 0:
            write_paginated_pdf_streaming(file_paths, temporary, settings, title=output_path.stem)
            os.replace(temporary, output_path)
            # The streaming path intentionally skips the giant stitched canvas.
            result = None
            images = []
        else:
            images = load_images(file_paths)
            result, bounds = stitch_images(images, overlap_px=settings.overlap_px)

            if settings.connect_lines:
                connected = detect_and_connect_image(
                    result,
                    settings.line_thickness,
                    settings.y_tolerance,
                    settings.line_color,
                )
                result.close()
                result = connected
            if settings.remove_yellow:
                cleaned = remove_yellow_pixels(result)
                result.close()
                result = cleaned

            if export_mode == ExportMode.PDF:
                pages = generate_pdf_pages(result, bounds, dpi=settings.dpi)
                temporary.write_bytes(pages_to_pdf_bytes(pages, dpi=settings.dpi))
            elif export_mode == ExportMode.A4_PNG:
                page = prepare_printable_a4(result, dpi=settings.dpi)
                try:
                    page.save(temporary, format="PNG", dpi=(settings.dpi, settings.dpi))
                finally:
                    page.close()
            else:
                flattened = flatten_transparency(result)
                try:
                    flattened.save(temporary, format="PNG", dpi=(settings.dpi, settings.dpi))
                finally:
                    flattened.close()
            os.replace(temporary, output_path)
    except Exception:
        LOGGER.exception("Stitch export failed for %s", output_path)
        raise
    finally:
        for page in pages:
            page.close()
        if result is not None:
            result.close()
        for image in images:
            image.close()
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            LOGGER.warning("Could not clean temporary file %s", temporary, exc_info=True)

    if delete_originals:
        for file_path in file_paths:
            try:
                Path(file_path).unlink()
            except OSError as exc:
                LOGGER.warning("Could not delete original %s: %s", file_path, exc)

    LOGGER.info("Saved stitch export to %s", output_path)
    return output_path
