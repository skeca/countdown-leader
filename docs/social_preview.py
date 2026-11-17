"""Render the GitHub social preview image (1280x640) from the leader artwork.

    .venv/bin/python docs/social_preview.py

Writes docs/figures/social-preview.png. Upload it under Settings > General >
Social preview; GitHub does not read it from the repo.
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import generate as g  # noqa: E402

W, H = 1280, 640
OUT = ROOT / "docs" / "figures" / "social-preview.png"
BODY_FONT = ROOT / "fonts" / "Inter-Medium.ttf"


def dial(size: int) -> Image.Image:
    """A mid-sweep '8' without the calibration overlay, which needs a 16:9 frame."""
    overlay = g._draw_calibration_overlay
    g._draw_calibration_overlay = lambda *a, **k: None
    try:
        return g._render_dial_active(size, size, "8", 0.36, "")
    finally:
        g._draw_calibration_overlay = overlay


def fit_title(text: str, width: int, start: int) -> ImageFont.FreeTypeFont:
    size = start
    while size > 8:
        font = g._load_digit_font(size)
        l, _, r, _ = font.getbbox(text)
        if r - l <= width:
            return font
        size -= 2
    return font


def main() -> None:
    img = Image.new("RGB", (W, H), g.POP_BG)
    img.paste(dial(H), (0, 0))
    d = ImageDraw.Draw(img)

    x0, pad = H + 72, 72
    text_w = W - x0 - pad

    title = fit_title("COUNTDOWN", text_w, 220)
    body = ImageFont.truetype(str(BODY_FONT), 26)
    title_lines = ["COUNTDOWN", "LEADER"]
    body_lines = [
        ("Head and tail leaders with a 2-pop", (235, 235, 235)),
        ("ProRes 422 HQ · BT.709 · 48 kHz", (150, 150, 150)),
        ("HD · UHD · DCI · 23.976 / 24 / 25 fps", (150, 150, 150)),
    ]
    cap = title.getbbox("C")[3] - title.getbbox("C")[1]
    title_gap, body_step, bar_h = int(cap * 0.16), 40, 22
    title_h = len(title_lines) * cap + (len(title_lines) - 1) * title_gap
    block_h = title_h + 44 + len(body_lines) * body_step + 24 + bar_h
    y = (H - block_h) // 2

    for line in title_lines:
        l, t, _, _ = title.getbbox(line)
        d.text((x0 - l, y - t), line, font=title, fill=g.POP_INK)
        y += cap + title_gap
    y += 44 - title_gap

    for text, color in body_lines:
        d.text((x0, y), text, font=body, fill=color, anchor="la")
        y += body_step
    y += 24

    # 100% bars under the text, as on the leader itself.
    n = len(g.SMPTE_BARS)
    for i, color in enumerate(g.SMPTE_BARS):
        bx0 = x0 + round(i * text_w / n)
        bx1 = x0 + round((i + 1) * text_w / n)
        d.rectangle([bx0, y, bx1 - 1, y + bar_h], fill=color)
    d.rectangle([x0, y, x0 + text_w - 1, y + bar_h], outline=(90, 90, 90))

    img.save(OUT, optimize=True)
    print(f"{OUT.relative_to(ROOT)} {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
