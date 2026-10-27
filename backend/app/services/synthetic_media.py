"""Synthetic evidence imagery for the demo/live-inspection feed.

Draws a stylized "product under inspection" frame (brushed-metal part on a
conveyor background) with defect marks placed exactly at the detection
bounding boxes, so SVG overlays on the frontend line up with the pixels.
Only Pillow is required; images are returned as JPEG bytes.
"""

from __future__ import annotations

import io
import math
import random
from typing import Sequence

from PIL import Image, ImageDraw, ImageFilter

DEFAULT_SIZE = (640, 480)


def render_part_image(
    defect_boxes: Sequence[tuple[float, float, float, float]] = (),
    size: tuple[int, int] = DEFAULT_SIZE,
    rng: random.Random | None = None,
) -> bytes:
    """Render a synthetic part photo; ``defect_boxes`` are (x1, y1, x2, y2)."""
    rng = rng or random.Random()
    width, height = size
    image = Image.new("RGB", size)

    draw = ImageDraw.Draw(image)
    # Conveyor background: dark graphite with subtle horizontal texture.
    for y in range(height):
        shade = 38 + int(10 * math.sin(y / 9.0)) + rng.randint(-2, 2)
        draw.line([(0, y), (width, y)], fill=(shade, shade + 2, shade))

    # Part silhouette: brushed aluminum rounded rectangle, off-center like a
    # real belt shot would be.
    margin_x, margin_y = int(width * 0.10), int(height * 0.14)
    part_box = (margin_x, margin_y, width - margin_x, height - margin_y)
    part_tone = rng.randint(-8, 8)
    draw.rounded_rectangle(part_box, radius=26, fill=(176 + part_tone, 180 + part_tone, 186 + part_tone))
    draw.rounded_rectangle(part_box, radius=26, outline=(120, 124, 130), width=3)

    # Brushed-metal streaks, blended only inside the part silhouette.
    streaks = Image.new("RGB", size, (0, 0, 0))
    streak_draw = ImageDraw.Draw(streaks)
    x0, y0, x1, y1 = part_box
    for _ in range(90):
        y = rng.randint(y0 + 4, y1 - 4)
        brightness = rng.randint(-14, 14)
        streak_draw.line(
            [(x0 + 6, y), (x1 - 6, y)],
            fill=(128 + brightness, 128 + brightness, 128 + brightness),
            width=1,
        )
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(part_box, radius=26, fill=255)
    base = image.copy()
    image = Image.composite(Image.blend(base, streaks, 0.18), base, mask)

    draw = ImageDraw.Draw(image)
    # Corner bolts.
    for cx, cy in (
        (x0 + 18, y0 + 18),
        (x1 - 18, y0 + 18),
        (x0 + 18, y1 - 18),
        (x1 - 18, y1 - 18),
    ):
        draw.ellipse((cx - 7, cy - 7, cx + 7, cy + 7), fill=(96, 100, 106))
        draw.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=(70, 74, 80))

    # Stamped part code.
    draw.text((x0 + 34, y0 + 26), f"PART-{rng.randint(1000, 9999)}", fill=(84, 88, 94))

    # Defects: dark irregular blobs with a highlight rim, aligned to boxes.
    for box in defect_boxes:
        bx1, by1, bx2, by2 = (int(v) for v in box)
        bx1, by1 = max(bx1, x0 + 4), max(by1, y0 + 4)
        bx2, by2 = min(bx2, x1 - 4), min(by2, y1 - 4)
        if bx2 <= bx1 or by2 <= by1:
            continue
        defect = Image.new("RGBA", size, (0, 0, 0, 0))
        defect_draw = ImageDraw.Draw(defect)
        for _ in range(14):
            jitter_x = rng.randint(-6, 6)
            jitter_y = rng.randint(-6, 6)
            radius = rng.randint(2, max(3, min(bx2 - bx1, by2 - by1) // 3))
            cx = rng.randint(bx1 + 2, max(bx1 + 3, bx2 - 2))
            cy = rng.randint(by1 + 2, max(by1 + 3, by2 - 2))
            defect_draw.ellipse(
                (cx - radius + jitter_x, cy - radius + jitter_y, cx + radius + jitter_x, cy + radius + jitter_y),
                fill=(38, 30, 24, rng.randint(150, 230)),
            )
        defect_draw.ellipse((bx1, by1, bx2, by2), outline=(58, 44, 32, 220), width=2)
        defect = defect.filter(ImageFilter.GaussianBlur(1.1))
        image.paste(defect, (0, 0), defect)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=86)
    return buffer.getvalue()
