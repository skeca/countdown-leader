#!/usr/bin/env python3
"""Generate custom editorial leaders with a frame-accurate stereo sync pop.

Head leaders: SMPTE-style countdown or simple flash, pop 2 s before picture.
Tail leader: placed after the last frame of action, pop 2 s after it.
ProRes HQ / BT.709 (tagged in the bitstream and the MOV colr atom),
48 kHz 24-bit PCM, 1 kHz at -20 dBFS.
24000/1001 keeps nominal frame counts: 192 frames, 48-frame pop offset.
"""

from __future__ import annotations

import argparse
import io
import math
import subprocess
import tempfile
from fractions import Fraction
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from media_spec import STYLES, active_rect, plan

FONTS_DIR = Path(__file__).parent / "fonts"
DIGIT_FONT_PATH = FONTS_DIR / "BigShouldersDisplay.ttf"
DIGIT_FONT_VARIATION = b"Bold"
LABEL_FONT_PATH = FONTS_DIR / "Inter-Medium.ttf"


def _load_digit_font(size: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(DIGIT_FONT_PATH), size)
    try:
        f.set_variation_by_name(DIGIT_FONT_VARIATION)
    except Exception:
        pass
    return f


def _draw_clock_lines(
    d: ImageDraw.ImageDraw,
    center: tuple[float, float],
    waiting_top: tuple[float, float],
    hand_end: tuple[float, float],
    color: tuple[int, int, int],
    width: int,
) -> None:
    """Draw the waiting line and clock hand as a single connected polyline
    with a curved joint at the dial center. This avoids the butt-cap notch
    that appears when two separate lines meet at the pivot.
    Also adds simulated round end caps so the far tips aren't flat-cut.
    """
    d.line([waiting_top, center, hand_end], fill=color, width=width, joint="curve")
    r = width / 2
    for (x, y) in (waiting_top, hand_end):
        d.ellipse([x - r, y - r, x + r, y + r], fill=color)

SAMPLE_RATE = 48_000
POP_FREQ_HZ = 1_000
POP_DBFS = -20.0  # SMPTE RP 155 US alignment level; override with --pop-dbfs
SUPERSAMPLE = 2  # hard-edged dial only needs modest SSAA

# Colors are authored in FULL range (0 = black, 255 = white). The ffmpeg
# encode maps this rgb24 input into legal BT.709 limited range, so 0 -> code
# 16 and 255 -> code 235. Authoring the *legal* code values (16/235) here
# would double-compress the range, so we feed 0/255 and let the encoder place
# them on the legal floor/ceiling.
GRAY_BG = (229, 229, 229)
WEDGE_FILL = (190, 190, 190)
INK = (0, 0, 0)
CIRCLE_COLOR = (250, 250, 250)
CROSSHAIR_UNPASSED = (170, 170, 170)   # subtle gray, visible against light bg
CROSSHAIR_PASSED = (245, 245, 245)     # brighter, visible against wedge fill
POP_BG = (0, 0, 0)            # pure black -> code 16, matches the black tail
POP_INK = (255, 255, 255)     # full white -> code 235 (legal peak)

# Calibration widgets. Full-range endpoints so the encode lands the bars on
# the true legal floor/ceiling (16 / 235).
LO, HI = 0, 255
SMPTE_BARS = [
    (HI, HI, HI),  # W
    (HI, HI, LO),  # Y
    (LO, HI, HI),  # C
    (LO, HI, LO),  # G
    (HI, LO, HI),  # M
    (HI, LO, LO),  # R
    (LO, LO, HI),  # B
    (LO, LO, LO),  # K
]
SMPTE_LABELS = ["W", "Y", "C", "G", "M", "R", "B", "K"]
# Grayscale step wedge: 11 chips spanning 0-100% of the legal range in 10%
# steps. Labeled as a percentage of the legal 16-235 window (0% = code 16,
# 100% = code 235) rather than raw 8-bit values, which the encoder remaps.
STEP_WEDGE_PCT = list(range(0, 101, 10))  # 0, 10, ..., 100
STEP_WEDGE = [(round(p * 255 / 100),) * 3 for p in STEP_WEDGE_PCT]

DIAL_R_FRAC = 0.40
DIAL_INNER_FRAC = 0.78


# ---------------------------------------------------------------------------
# Frame-rate / geometry helpers
# ---------------------------------------------------------------------------


def canonical_rate(fps: float | Fraction) -> Fraction:
    value = float(fps)
    for alias, rate in ((23.976, Fraction(24000, 1001)),
                        (24000 / 1001, Fraction(24000, 1001)),
                        (24.0, Fraction(24)), (25.0, Fraction(25))):
        if math.isfinite(value) and abs(value - alias) < 1e-7:
            return rate
    raise ValueError("fps must be 23.976 (24000/1001), 24, or 25")


def fps_to_num_den(fps: float) -> tuple[int, int]:
    rate = canonical_rate(fps)
    return rate.numerator, rate.denominator


def fps_label(fps: float) -> str:
    rate = canonical_rate(fps)
    return "2397" if rate.denominator != 1 else str(rate.numerator)


def fps_display(fps: float) -> str:
    rate = canonical_rate(fps)
    return "23.976FPS" if rate.denominator != 1 else f"{rate.numerator}FPS"


def frames_per_second_int(fps: float) -> int:
    return round(canonical_rate(fps))


def validate_options(fps, W, H, active_aspect, pop_dbfs):
    canonical_rate(fps)
    if not isinstance(W, int) or not isinstance(H, int) or W < 64 or H < 64 or W % 2 or H % 2:
        raise ValueError("width and height must be even integers of at least 64 pixels")
    if active_aspect is not None:
        if not math.isfinite(active_aspect) or active_aspect <= 0:
            raise ValueError("active aspect must be finite and positive")
        rect = active_rect(W, H, active_aspect)
        if min(rect[2] - rect[0], rect[3] - rect[1]) < 64:
            raise ValueError("active area must be at least 64 pixels in each dimension")
    if not math.isfinite(pop_dbfs) or not -60 <= pop_dbfs <= 0:
        raise ValueError("pop level must be finite and between -60 and 0 dBFS")


# ---------------------------------------------------------------------------
# Frame plan
# ---------------------------------------------------------------------------


def plan_for(style: str, fps: float) -> tuple[int, int]:
    """(total frames, pop frame); see media_spec.plan."""
    return plan(style, frames_per_second_int(fps))


def plan_smpte(fps: float) -> tuple[int, int]:
    return plan_for("smpte", fps)


def plan_simple(fps: float) -> tuple[int, int]:
    return plan_for("simple", fps)


def smpte_spec(idx: int, fps: float) -> tuple[str, str | None, float]:
    """Return (kind, digit, sweep_phase) for SMPTE frame `idx`.

    PICTURE START appears on frame 0, followed by "8" with the full dial.
    Each subsequent numbered second (7..3) opens with one digit-only frame,
    followed by the dial and a clockwise sweep for the rest of the second.
    """
    fpsi = frames_per_second_int(fps)
    pop_idx = 6 * fpsi
    if idx == 0:
        return ("picture_start", None, 0.0)
    if idx == pop_idx:
        return ("two_pop", "2", 0.0)
    if idx > pop_idx:
        return ("black", None, 0.0)
    if idx < fpsi:
        # "8" second: dial throughout, phases 1/fpsi .. (fpsi-1)/fpsi.
        return ("dial", "8", idx / fpsi)
    sec = idx // fpsi  # 1..5 for digits 7..3
    digit = str(8 - sec)
    frame_in_sec = idx % fpsi  # 0..fpsi-1
    if frame_in_sec == 0:
        # First frame of each numbered second 7..3: digit alone, no dial.
        return ("digit_only", digit, 0.0)
    return ("dial", digit, frame_in_sec / fpsi)


def flash_spec(style: str, idx: int, fps: float) -> tuple[str, str | None, float]:
    """simple and tail: black except the single pop frame."""
    if idx == plan_for(style, fps)[1]:
        return ("two_pop", "2", 0.0)
    return ("black", None, 0.0)


# ---------------------------------------------------------------------------
# Active-area renderers (each returns an Image of size (aw, ah))
# ---------------------------------------------------------------------------


def _digit_size_for(sh: int) -> int:
    R = int(sh * DIAL_R_FRAC)
    r_inner = int(R * DIAL_INNER_FRAC)
    return int(r_inner * 1.55)


def _text_color_for_chip(color: tuple[int, int, int]) -> tuple[int, int, int]:
    """Pick a text color (dark or light) that reads against the chip background."""
    r, g, b = color
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return INK if luminance > 140 else (235, 235, 235)


def _draw_swatch_strip(
    d: ImageDraw.ImageDraw,
    sw: int,
    sh: int,
    colors: list,
    inset_labels: list | None,
    left: int,
    bottom: int,
    subtitle: str | None = None,
) -> None:
    """Draw a row of color chips.

    - inset_labels: if provided, draws each label ON its chip with adaptive
      (light or dark) text color. Pass None for no per-chip text.
    - subtitle: optional single-line label drawn below the strip.
    """
    n = len(colors)
    chip_w = int(sw * 0.038)
    chip_h = int(sh * 0.082)
    border = max(2, sw // 800)
    inset_size = int(sh * 0.026)
    inset_font = ImageFont.truetype(str(LABEL_FONT_PATH), inset_size)
    subtitle_size = int(sh * 0.020)
    subtitle_gap = int(sh * 0.010)

    total_w = chip_w * n
    if subtitle:
        y0 = bottom - subtitle_size - subtitle_gap - chip_h
    else:
        y0 = bottom - chip_h

    d.rectangle(
        [left - border, y0 - border, left + total_w + border, y0 + chip_h + border],
        outline=INK,
        width=border,
    )

    for i, color in enumerate(colors):
        x0 = left + i * chip_w
        d.rectangle([x0, y0, x0 + chip_w, y0 + chip_h], fill=color)
        if inset_labels:
            d.text(
                (x0 + chip_w // 2, y0 + chip_h // 2),
                str(inset_labels[i]),
                font=inset_font,
                fill=_text_color_for_chip(color),
                anchor="mm",
            )

    if subtitle:
        subtitle_font = ImageFont.truetype(str(LABEL_FONT_PATH), subtitle_size)
        d.text(
            (left + total_w // 2, bottom),
            subtitle,
            font=subtitle_font,
            fill=INK,
            anchor="ms",
        )


def _draw_calibration_overlay(
    d: ImageDraw.ImageDraw, sw: int, sh: int, fps_text: str
) -> None:
    """Draw the always-on overlay for countdown frames: FPS label upper-left,
    grayscale step wedge bottom-left, SMPTE color bars bottom-right. Rendered
    on every dial AND digit-only frame so the corner widgets don't flicker."""
    label_size = int(sh * 0.045)
    label_font = ImageFont.truetype(str(LABEL_FONT_PATH), label_size)
    d.text(
        (int(sw * 0.04), int(sh * 0.06)),
        fps_text,
        font=label_font,
        fill=INK,
        anchor="lt",
    )
    _draw_step_wedge(d, sw, sh)
    _draw_smpte_bars(d, sw, sh)


def _draw_step_wedge(d: ImageDraw.ImageDraw, sw: int, sh: int) -> None:
    _draw_swatch_strip(
        d,
        sw,
        sh,
        STEP_WEDGE,
        inset_labels=[str(p) for p in STEP_WEDGE_PCT],
        left=int(sw * 0.03),
        bottom=int(sh * 0.96),
    )


def _draw_smpte_bars(d: ImageDraw.ImageDraw, sw: int, sh: int) -> None:
    chip_w = int(sw * 0.038)
    chip_h = int(sh * 0.082)
    n = len(SMPTE_BARS)
    total_w = chip_w * n
    left = int(sw * 0.97) - total_w
    bottom = int(sh * 0.96)

    _draw_swatch_strip(
        d, sw, sh, SMPTE_BARS, inset_labels=None, left=left, bottom=bottom
    )

    # Amplitude label beside the strip, so no chip is covered and both
    # strips keep one baseline. Same face/size as the grayscale chip labels.
    label_font = ImageFont.truetype(str(LABEL_FONT_PATH), int(sh * 0.026))
    d.text(
        (left - int(sh * 0.018), bottom - chip_h // 2),
        "100%",
        font=label_font,
        fill=INK,
        anchor="rm",
    )


def _render_picture_start_active(aw: int, ah: int) -> Image.Image:
    """Inverted slate: broadcast-safe white text on black, near edge-to-edge.
    The inversion + scale is the entire statement — no rules or framing."""
    sw, sh = aw * SUPERSAMPLE, ah * SUPERSAMPLE
    big = Image.new("RGB", (sw, sh), POP_BG)
    d = ImageDraw.Draw(big)

    # Fit "PICTURE" to ~94% of the frame width.
    target_w = int(sw * 0.94)
    font_size = int(sh * 0.50)
    for _ in range(12):
        font = _load_digit_font(font_size)
        w = font.getbbox("PICTURE")[2] - font.getbbox("PICTURE")[0]
        if w <= target_w:
            break
        font_size = int(font_size * target_w / w)

    cx = sw // 2
    cy = sh // 2
    line_gap = int(font_size * 0.92)
    d.text((cx, cy - line_gap // 2), "PICTURE", font=font, fill=POP_INK, anchor="mm")
    d.text((cx, cy + line_gap // 2), "START", font=font, fill=POP_INK, anchor="mm")
    return big.resize((aw, ah), Image.LANCZOS)


def _render_digit_only_active(aw: int, ah: int, digit: str, fps_text: str) -> Image.Image:
    sw, sh = aw * SUPERSAMPLE, ah * SUPERSAMPLE
    big = Image.new("RGB", (sw, sh), GRAY_BG)
    d = ImageDraw.Draw(big)
    font_size = _digit_size_for(sh)
    font = _load_digit_font(font_size)
    d.text((sw // 2, sh // 2), digit, font=font, fill=INK, anchor="mm")
    _draw_calibration_overlay(d, sw, sh, fps_text)
    return big.resize((aw, ah), Image.LANCZOS)


def _render_dial_active(
    aw: int, ah: int, digit: str, sweep_phase: float, fps_text: str
) -> Image.Image:
    sw, sh = aw * SUPERSAMPLE, ah * SUPERSAMPLE
    big = Image.new("RGB", (sw, sh), GRAY_BG)
    d = ImageDraw.Draw(big)
    cx, cy = sw // 2, sh // 2
    R = int(sh * DIAL_R_FRAC)
    r_inner = int(R * DIAL_INNER_FRAC)
    stroke_circle = max(10, R // 18)
    stroke_hand = max(8, R // 50)
    stroke_crosshair = max(4, R // 90)        # thicker, more visible
    stroke_waiting = stroke_hand              # match hand thickness

    # Full-frame wedge sector.
    if sweep_phase > 1e-4:
        R_far = int(math.hypot(sw, sh))
        start_deg = -90.0
        end_deg = -90.0 + 360.0 * sweep_phase
        n_steps = max(8, int(abs(end_deg - start_deg) / 2))
        points = [(cx, cy)]
        for i in range(n_steps + 1):
            a = math.radians(start_deg + (end_deg - start_deg) * i / n_steps)
            points.append((cx + R_far * math.cos(a), cy + R_far * math.sin(a)))
        d.polygon(points, fill=WEDGE_FILL)

    # Crosshair segments — each colored by whether the hand has swept past it.
    # The hand sweeps clockwise from 12 o'clock, hitting 3 o'clock at phase 0.25,
    # 6 o'clock at 0.5, 9 o'clock at 0.75.
    right_color = CROSSHAIR_PASSED if sweep_phase >= 0.25 else CROSSHAIR_UNPASSED
    bottom_color = CROSSHAIR_PASSED if sweep_phase >= 0.50 else CROSSHAIR_UNPASSED
    left_color = CROSSHAIR_PASSED if sweep_phase >= 0.75 else CROSSHAIR_UNPASSED
    d.line([(cx, cy), (sw, cy)], fill=right_color, width=stroke_crosshair)
    d.line([(0, cy), (cx, cy)], fill=left_color, width=stroke_crosshair)
    d.line([(cx, cy), (cx, sh)], fill=bottom_color, width=stroke_crosshair)
    # Top vertical is replaced by the thick black "waiting" line further down.

    # White dial circles.
    d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=CIRCLE_COLOR, width=stroke_circle)
    d.ellipse(
        [cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner],
        outline=CIRCLE_COLOR,
        width=stroke_circle,
    )

    # Static "waiting" line at 12 o'clock + clock hand, drawn as a single
    # connected polyline with a curved joint at the dial center. Same stroke
    # width for both so the joint reads as one continuous element.
    a = math.radians(-90.0 + 360.0 * sweep_phase)
    R_hand = int(math.hypot(sw, sh))  # larger than any in-frame distance
    hand_end = (cx + R_hand * math.cos(a), cy + R_hand * math.sin(a))
    _draw_clock_lines(d, (cx, cy), (cx, 0), hand_end, INK, stroke_hand)

    # Center digit (drawn last so it sits cleanly over the pivot region).
    digit_size = _digit_size_for(sh)
    digit_font = _load_digit_font(digit_size)
    d.text((cx, cy), digit, font=digit_font, fill=INK, anchor="mm")

    _draw_calibration_overlay(d, sw, sh, fps_text)

    return big.resize((aw, ah), Image.LANCZOS)


def _render_two_pop_active(aw: int, ah: int) -> Image.Image:
    sw, sh = aw * SUPERSAMPLE, ah * SUPERSAMPLE
    big = Image.new("RGB", (sw, sh), POP_BG)
    d = ImageDraw.Draw(big)
    font_size = _digit_size_for(sh)
    font = _load_digit_font(font_size)
    d.text((sw // 2, sh // 2), "2", font=font, fill=POP_INK, anchor="mm")
    return big.resize((aw, ah), Image.LANCZOS)


# ---------------------------------------------------------------------------
# Top-level frame dispatch
# ---------------------------------------------------------------------------


def render_frame(
    style: str, idx: int, fps: float, W: int, H: int, rect, fps_text: str
) -> Image.Image:
    canvas = Image.new("RGB", (W, H), (0, 0, 0))
    x0, y0, x1, y1 = rect
    aw, ah = x1 - x0, y1 - y0

    if style == "smpte":
        kind, digit, phase = smpte_spec(idx, fps)
    else:
        kind, digit, phase = flash_spec(style, idx, fps)

    if kind == "black":
        return canvas
    if kind == "picture_start":
        active = _render_picture_start_active(aw, ah)
    elif kind == "dial":
        active = _render_dial_active(aw, ah, digit, phase, fps_text)
    elif kind == "digit_only":
        active = _render_digit_only_active(aw, ah, digit, fps_text)
    elif kind == "two_pop":
        active = _render_two_pop_active(aw, ah)
    else:
        return canvas

    canvas.paste(active, (x0, y0))
    return canvas


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------


def make_audio_wav_bytes(
    total_frames: int, fps: float, pop_frame_idx: int, pop_dbfs: float = POP_DBFS
) -> bytes:
    fps = canonical_rate(fps)
    duration_s = total_frames / fps
    n_samples = int(round(duration_s * SAMPLE_RATE))
    audio = np.zeros(n_samples, dtype=np.float32)

    pop_start = int(round((pop_frame_idx / fps) * SAMPLE_RATE))
    pop_end = int(round(((pop_frame_idx + 1) / fps) * SAMPLE_RATE))
    pop_end = min(pop_end, n_samples)
    pop_len = pop_end - pop_start
    if pop_len > 0:
        t = np.arange(pop_len, dtype=np.float32) / SAMPLE_RATE
        amplitude = 10 ** (pop_dbfs / 20.0)
        tone = amplitude * np.sin(2 * math.pi * POP_FREQ_HZ * t)
        # Raised-cosine 2 ms fades.
        fade_len = min(int(SAMPLE_RATE * 0.002), pop_len // 4)
        if fade_len > 0:
            fade = (
                0.5 - 0.5 * np.cos(np.linspace(0.0, math.pi, fade_len))
            ).astype(np.float32)
            tone[:fade_len] *= fade
            tone[-fade_len:] *= fade[::-1]
        audio[pop_start:pop_end] = tone

    stereo = np.stack([audio, audio], axis=1)
    pcm = np.clip(stereo, -1.0, 1.0)
    pcm_int = (pcm * (2**23 - 1)).astype(np.int32)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(3)
        w.setframerate(SAMPLE_RATE)
        out = bytearray()
        for sample in pcm_int.flatten():
            sample = int(sample) & 0xFFFFFF
            out.append(sample & 0xFF)
            out.append((sample >> 8) & 0xFF)
            out.append((sample >> 16) & 0xFF)
        w.writeframes(bytes(out))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Encode driver
# ---------------------------------------------------------------------------


def encode(
    style: str,
    fps: float,
    W: int,
    H: int,
    active_aspect: float | None,
    out_path: Path,
    pop_dbfs: float = POP_DBFS,
) -> None:
    validate_options(fps, W, H, active_aspect, pop_dbfs)
    fps = canonical_rate(fps)
    rect = active_rect(W, H, active_aspect)
    if style not in STYLES:
        raise SystemExit(f"unknown style: {style}")
    total, pop_idx = plan_for(style, fps)

    fps_text = fps_display(fps)
    wav_bytes = make_audio_wav_bytes(total, fps, pop_idx, pop_dbfs)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Same filesystem as the destination: replacement is atomic. Unique paths
    # also isolate simultaneous renders of the same destination.
    with tempfile.TemporaryDirectory(prefix=".leader-", dir=out_path.parent) as tmp:
        _encode_staged(style, fps, W, H, rect, fps_text, total, wav_bytes,
                       Path(tmp), out_path, pop_dbfs)


def _encode_staged(style, fps, W, H, rect, fps_text, total, wav_bytes, stage, out_path, pop_dbfs):
    audio_path = stage / "audio.wav"
    movie_path = stage / "movie.mov"
    audio_path.write_bytes(wav_bytes)
    num, den = fps_to_num_den(fps)
    fps_arg = f"{num}/{den}"
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{W}x{H}",
        "-r",
        fps_arg,
        "-i",
        "-",
        "-i",
        str(audio_path),
        "-c:v",
        "prores_ks",
        "-profile:v",
        "3",  # ProRes 422 HQ
        "-pix_fmt",
        "yuv422p10le",
        "-vf",
        # setparams tags the frames, so the muxer writes colr nclc 1-1-1;
        # -color_* alone reaches neither the MOV atom nor the ProRes header.
        "scale=in_range=full:out_range=tv:out_color_matrix=bt709,"
        "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv,"
        "setsar=1",
        "-threads:v",
        "2",
        "-filter_threads",
        "1",
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-color_range",
        "tv",
        "-c:a",
        "pcm_s24le",
        # Write BT.709 primaries/transfer into the ProRes header
        # (the container-level tags alone don't cover all four fields).
        "-bsf:v",
        "prores_metadata=color_primaries=bt709:color_trc=bt709:colorspace=bt709",
        "-r",
        fps_arg,
        "-movflags",
        "+faststart",
        str(movie_path),
    ]

    # Popen's context manager reaps the encoder even if rendering/write fails.
    with subprocess.Popen(cmd, stdin=subprocess.PIPE) as proc:
        assert proc.stdin is not None
        try:
            for idx in range(total):
                img = render_frame(style, idx, fps, W, H, rect, fps_text)
                proc.stdin.write(img.tobytes())
            proc.stdin.close()
            rc = proc.wait()
            if rc:
                raise RuntimeError(f"ffmpeg exited with code {rc}")
        except BaseException:
            if proc.poll() is None:
                proc.terminate()
            raise
    # Validate the completed output before replacing a previous good version.
    from verify_media import inspect_media
    result = inspect_media(movie_path, style=style, fps_label_value=fps_label(fps),
                           size=(W, H), pop_dbfs=pop_dbfs)
    if result["errors"]:
        raise RuntimeError(f"render validation failed: {result['errors']}")
    movie_path.replace(out_path)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--style", choices=list(STYLES), required=True)
    p.add_argument("--fps", type=float, required=True)
    p.add_argument("--width", type=int, required=True)
    p.add_argument("--height", type=int, required=True)
    p.add_argument("--active-aspect", type=float, default=None)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument(
        "--pop-dbfs",
        type=float,
        default=POP_DBFS,
        help="Peak level of the 1 kHz sync pop, in dBFS "
        "(default %(default)s; SMPTE/US -20, EBU/EU -18).",
    )
    args = p.parse_args()
    try:
        validate_options(args.fps, args.width, args.height, args.active_aspect, args.pop_dbfs)
    except ValueError as exc:
        p.error(str(exc))
    return args


def main() -> None:
    args = parse_args()
    encode(
        args.style,
        args.fps,
        args.width,
        args.height,
        args.active_aspect,
        args.out,
        args.pop_dbfs,
    )


if __name__ == "__main__":
    main()
