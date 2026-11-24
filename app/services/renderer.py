from __future__ import annotations

import logging
import random
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.config import get_settings
from app.services.tts import estimate_duration, synthesize

logger = logging.getLogger(__name__)

WIDTH = 1080
HEIGHT = 1920
FPS = 30
PALETTES = [
    ((18, 18, 28), (255, 214, 10), (248, 248, 248)),
    ((8, 28, 42), (0, 200, 180), (250, 250, 250)),
    ((42, 12, 18), (255, 107, 107), (255, 245, 238)),
    ((16, 32, 16), (163, 230, 53), (240, 253, 244)),
    ((24, 16, 40), (196, 181, 253), (250, 245, 255)),
]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = (current + " " + word).strip()
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [text]


def make_slide(path: Path, hook: str, body: str, caption: str, variant: int, attribution: str) -> Path:
    bg, accent, fg = PALETTES[variant % len(PALETTES)]
    img = Image.new("RGB", (WIDTH, HEIGHT), bg)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, WIDTH, 28), fill=accent)
    draw.rectangle((0, HEIGHT - 220, WIDTH, HEIGHT), fill=(0, 0, 0))
    title_font = _font(64, bold=True)
    body_font = _font(44)
    cap_font = _font(40, bold=True)
    small = _font(24)

    y = 180
    for line in _wrap(draw, hook, title_font, WIDTH - 120)[:4]:
        draw.text((60, y), line, font=title_font, fill=accent)
        y += 80
    y += 40
    for line in _wrap(draw, body, body_font, WIDTH - 120)[:10]:
        draw.text((60, y), line, font=body_font, fill=fg)
        y += 58

    # Keep captions above typical YouTube UI (~180px).
    cap_lines = _wrap(draw, caption, cap_font, WIDTH - 140)[:3]
    cap_y = HEIGHT - 200
    for line in cap_lines:
        w = draw.textlength(line, font=cap_font)
        draw.text(((WIDTH - w) / 2, cap_y), line, font=cap_font, fill=(255, 255, 255))
        cap_y += 48
    if attribution:
        draw.text((40, HEIGHT - 36), attribution[:90], font=small, fill=(180, 180, 180))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")
    return path


def _chunk_words(text: str, size: int = 5) -> list[str]:
    words = text.split()
    return [" ".join(words[i : i + size]) for i in range(0, len(words), size)] or [text]


def render_short(
    video_id: int,
    hook: str,
    body: str,
    language: str = "en",
    attribution: str = "Original graphics / VIRALSHORTS AI",
) -> dict:
    settings = get_settings()
    work = settings.render_root / f"job-{video_id}"
    work.mkdir(parents=True, exist_ok=True)
    full = f"{hook} {body}".strip()
    audio = synthesize(full, work / "voice.wav", language=language)
    duration = estimate_duration(full)
    chunks = _chunk_words(full, 6)
    frames_dir = work / "frames"
    frames_dir.mkdir(exist_ok=True)
    variant = random.randint(0, len(PALETTES) - 1)
    slides: list[Path] = []
    for idx, chunk in enumerate(chunks[:8]):
        slides.append(
            make_slide(
                frames_dir / f"{idx:03d}.png",
                hook if idx == 0 else chunks[max(0, idx - 1)],
                body if idx else hook,
                chunk,
                variant + idx,
                attribution,
            )
        )
    concat = work / "slides.txt"
    per = max(1.6, duration / max(1, len(slides)))
    with concat.open("w") as fh:
        for slide in slides:
            fh.write(f"file '{slide.resolve()}'\n")
            fh.write(f"duration {per:.2f}\n")
        fh.write(f"file '{slides[-1].resolve()}'\n")

    out = settings.render_root / f"short-{video_id}.mp4"
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not installed")
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat),
        "-i",
        str(audio),
        "-vf",
        f"scale={WIDTH}:{HEIGHT},fps={FPS},format=yuv420p,zoompan=z='min(zoom+0.0008,1.08)':d=1:s={WIDTH}x{HEIGHT}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-c:a",
        "aac",
        "-shortest",
        "-movflags",
        "+faststart",
        str(out),
    ]
    # zoompan + concat can be fragile; fall back to a simpler filter if needed.
    simple = [
        ffmpeg,
        "-y",
        "-loop",
        "1",
        "-i",
        str(slides[0]),
        "-i",
        str(audio),
        "-vf",
        f"scale={WIDTH}:{HEIGHT},fps={FPS},format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-t",
        f"{min(45, max(8, duration)):.2f}",
        "-c:a",
        "aac",
        "-shortest",
        "-movflags",
        "+faststart",
        str(out),
    ]
    try:
        subprocess.run(simple, check=True, capture_output=True, timeout=90)
    except subprocess.CalledProcessError as exc:
        logger.error("ffmpeg failed: %s", exc.stderr[-400:] if exc.stderr else exc)
        raise RuntimeError("video render failed") from exc
    if not out.exists() or out.stat().st_size < 1000:
        raise RuntimeError("renderer produced an empty file")
    return {
        "path": str(out),
        "duration": min(45, max(8, duration)),
        "width": WIDTH,
        "height": HEIGHT,
        "slides": len(slides),
    }
