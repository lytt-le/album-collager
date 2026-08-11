"""Grid collage renderer built on Pillow."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from PIL import Image, ImageColor

from .library import Album

# Pillow refuses very large images by default as a decompression-bomb guard.
Image.MAX_IMAGE_PIXELS = None


@dataclass
class CollageOptions:
    cell_size: int = 1000        # px per cover, before gaps
    gap: int = 0                 # px between covers
    margin: int = 0              # px border around the whole grid
    columns: int = 0             # 0 = auto (as square as possible)
    background: str = "#000000"
    max_pixels: int = 0          # 0 = no cap; otherwise downscale to fit


def grid_shape(count: int, columns: int = 0) -> tuple[int, int]:
    """Return (columns, rows) for `count` items."""
    if count <= 0:
        return 0, 0
    cols = columns if columns > 0 else max(1, int(math.ceil(math.sqrt(count))))
    rows = int(math.ceil(count / cols))
    return cols, rows


def output_size(count: int, opts: CollageOptions) -> tuple[int, int]:
    cols, rows = grid_shape(count, opts.columns)
    if not cols:
        return 0, 0
    width = cols * opts.cell_size + (cols - 1) * opts.gap + 2 * opts.margin
    height = rows * opts.cell_size + (rows - 1) * opts.gap + 2 * opts.margin
    return width, height


def _fit_square(img: Image.Image, size: int) -> Image.Image:
    """Center-crop to a square then resize - covers are square but scans vary."""
    w, h = img.size
    if w != h:
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))
    if img.size != (size, size):
        resample = Image.LANCZOS if img.width >= size else Image.BICUBIC
        img = img.resize((size, size), resample)
    return img


def build(albums: Sequence[Album], opts: CollageOptions,
          progress: Callable[[int, int], None] | None = None,
          should_cancel: Callable[[], bool] | None = None,
          use_thumbs: bool = False) -> Image.Image:
    """Render the collage and return the finished Pillow image."""
    count = len(albums)
    if count == 0:
        raise ValueError("Add at least one album before exporting a collage.")

    cols, rows = grid_shape(count, opts.columns)
    width, height = output_size(count, opts)

    try:
        bg = ImageColor.getrgb(opts.background)
    except ValueError:
        bg = (0, 0, 0)

    canvas = Image.new("RGB", (width, height), bg)

    for index, album in enumerate(albums):
        if should_cancel and should_cancel():
            raise InterruptedError("Collage export cancelled.")
        col, row = index % cols, index // cols
        x = opts.margin + col * (opts.cell_size + opts.gap)
        y = opts.margin + row * (opts.cell_size + opts.gap)
        path = album.cover_path
        if use_thumbs and album.thumb_file and album.thumb_path.exists():
            path = album.thumb_path
        try:
            with Image.open(path) as img:
                img.load()
                tile = _fit_square(img.convert("RGB"), opts.cell_size)
                canvas.paste(tile, (x, y))
        except (OSError, ValueError):
            # Missing or corrupt cover: leave the background showing through.
            pass
        if progress:
            progress(index + 1, count)

    if opts.max_pixels and width * height > opts.max_pixels:
        scale = math.sqrt(opts.max_pixels / (width * height))
        canvas = canvas.resize((max(1, int(width * scale)), max(1, int(height * scale))),
                               Image.LANCZOS)
    return canvas


def export_png(albums: Sequence[Album], opts: CollageOptions, out_path: str | Path,
               progress: Callable[[int, int], None] | None = None,
               should_cancel: Callable[[], bool] | None = None) -> Path:
    image = build(albums, opts, progress=progress, should_cancel=should_cancel)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path, "PNG", optimize=True)
    return out_path


def preview(albums: Sequence[Album], opts: CollageOptions, max_side: int = 900) -> Image.Image:
    """Fast, low-resolution render for the on-screen preview."""
    cols, rows = grid_shape(len(albums), opts.columns)
    if not cols:
        raise ValueError("Nothing to preview.")
    long_side = max(cols, rows)
    cell = max(24, min(opts.cell_size, int(max_side / long_side)))
    ratio = cell / opts.cell_size if opts.cell_size else 1
    small = CollageOptions(
        cell_size=cell,
        gap=int(round(opts.gap * ratio)),
        margin=int(round(opts.margin * ratio)),
        columns=opts.columns,
        background=opts.background,
    )
    return build(albums, small, use_thumbs=True)
