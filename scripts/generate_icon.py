#!/usr/bin/env python3
"""Generate pixel-aware SU2CAD PNG/ICO application icons."""

from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
FONT_CANDIDATES = (
    Path(r"C:\Windows\Fonts\seguisb.ttf"),
    Path(r"C:\Windows\Fonts\arialbd.ttf"),
)


def font_path() -> Path:
    return next((path for path in FONT_CANDIDATES if path.is_file()), FONT_CANDIDATES[-1])


def render_icon(size: int) -> Image.Image:
    scale = 4
    canvas_size = size * scale
    image = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def box(values: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
        return tuple(round(value * canvas_size) for value in values)  # type: ignore[return-value]

    outline_width = max(scale, round(size * 0.025 * scale))
    draw.rounded_rectangle(
        box((0.035, 0.035, 0.965, 0.965)),
        radius=round(canvas_size * 0.20),
        fill="#1976E9",
        outline="#0B4FAF",
        width=outline_width,
    )

    if size >= 24:
        draw.rounded_rectangle(
            box((0.205, 0.195, 0.835, 0.845)),
            radius=round(canvas_size * 0.09),
            fill="#083E91",
        )
    draw.rounded_rectangle(
        box((0.18, 0.16, 0.81, 0.81)),
        radius=round(canvas_size * 0.09),
        fill="#FFFFFF",
    )

    if size >= 48:
        draw.polygon(
            [
                (round(canvas_size * 0.66), round(canvas_size * 0.16)),
                (round(canvas_size * 0.81), round(canvas_size * 0.31)),
                (round(canvas_size * 0.66), round(canvas_size * 0.31)),
            ],
            fill="#BFE3FF",
        )
        draw.line(
            [
                (round(canvas_size * 0.66), round(canvas_size * 0.16)),
                (round(canvas_size * 0.66), round(canvas_size * 0.31)),
                (round(canvas_size * 0.81), round(canvas_size * 0.31)),
            ],
            fill="#3497E8",
            width=max(scale, round(canvas_size * 0.012)),
        )

    label = "S" if size <= 16 else "S2"
    label_size = 0.37 if size <= 20 else 0.33
    font = ImageFont.truetype(str(font_path()), max(7 * scale, round(canvas_size * label_size)))
    bounds = draw.textbbox((0, 0), label, font=font, stroke_width=0)
    text_width = bounds[2] - bounds[0]
    text_height = bounds[3] - bounds[1]
    x = (canvas_size - text_width) / 2
    y = canvas_size * (0.47 if size >= 48 else 0.45) - text_height / 2 - bounds[1]
    draw.text((round(x), round(y)), label, font=font, fill="#155AB6")

    accent_height = max(scale, round(canvas_size * 0.035))
    draw.rounded_rectangle(
        box((0.31, 0.70, 0.69, 0.70 + accent_height / canvas_size)),
        radius=accent_height // 2,
        fill="#16C4B5",
    )

    return image.resize((size, size), Image.Resampling.LANCZOS)


def write_multi_png_ico(path: Path, frames: list[Image.Image]) -> None:
    png_blobs: list[bytes] = []
    for frame in frames:
        stream = io.BytesIO()
        frame.save(stream, format="PNG", optimize=True)
        png_blobs.append(stream.getvalue())

    header_size = 6 + (16 * len(frames))
    offset = header_size
    entries: list[bytes] = []
    for frame, blob in zip(frames, png_blobs):
        width = 0 if frame.width >= 256 else frame.width
        height = 0 if frame.height >= 256 else frame.height
        entries.append(
            struct.pack("<BBBBHHII", width, height, 0, 0, 1, 32, len(blob), offset)
        )
        offset += len(blob)

    path.write_bytes(
        struct.pack("<HHH", 0, 1, len(frames))
        + b"".join(entries)
        + b"".join(png_blobs)
    )


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    frames = [render_icon(size) for size in SIZES]
    for size, frame in zip(SIZES, frames):
        frame.save(ASSETS / f"su2cad-{size}.png", optimize=True)
    write_multi_png_ico(ASSETS / "su2cad.ico", frames)


if __name__ == "__main__":
    main()
