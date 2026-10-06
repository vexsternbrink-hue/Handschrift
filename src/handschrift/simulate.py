"""Simulated handwriting for the demo / self-test.

Fills the real template with characters from a handwriting-style font (every
character slightly rotated, sheared, shifted and resized so no two look the
same), then turns the clean page into a fake phone photo (perspective,
uneven light, colour cast, noise, blur, JPEG). Training on that photo
exercises exactly the same code path as a real photo.
"""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import layout
from .config import FONTS_DIR
from .template import build_template_pdf, rasterize_pdf

log = logging.getLogger(__name__)

SIM_PPM = 8.0  # resolution of the simulated "scan"
DEMO_FONTS = {
    "demo": "Caveat-Regular.ttf",
    "demo2": "PatrickHand-Regular.ttf",
}


def load_font(font_file: str | Path | None, size_px: int) -> ImageFont.FreeTypeFont:
    if font_file:
        path = Path(font_file)
        if not path.is_absolute():
            path = FONTS_DIR / path
        if path.is_file():
            return ImageFont.truetype(str(path), size_px)
        log.warning("Schrift %s nicht gefunden – nutze eingebaute Ersatzschrift", path)
    return ImageFont.load_default(size_px)


def _x_height_ratio(font_file) -> float:
    f = load_font(font_file, 200)
    l, t, r, b = f.getbbox("x", anchor="ls")
    return max(0.25, -t / 200.0)


def _render_char(ch: str, font: ImageFont.FreeTypeFont, rng: np.random.Generator):
    """Glyph coverage (float 0..1) on a canvas whose point (cx, cy) is the pen baseline origin."""
    size = font.size
    canvas = Image.new("L", (size * 3, size * 3), 0)
    cx, cy = size, int(size * 1.9)
    ImageDraw.Draw(canvas).text((cx, cy), ch, fill=255, font=font, anchor="ls")
    # random affine distortion around the baseline origin
    angle = np.deg2rad(rng.normal(0, 2.5))
    shear = rng.normal(0.0, 0.06)
    sx = rng.uniform(0.92, 1.08)
    sy = sx * rng.uniform(0.95, 1.05)
    A = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]]) @ np.array([[sx, shear], [0, sy]])
    M = np.hstack([A, (np.array([cx, cy]) - A @ np.array([cx, cy]))[:, None]])
    arr = cv2.warpAffine(np.asarray(canvas, np.float32) / 255.0, M, canvas.size, flags=cv2.INTER_LINEAR)
    # slight stroke-width variation
    k = int(rng.integers(0, 3))
    if k == 2:
        arr = cv2.dilate(arr, np.ones((2, 2), np.uint8))
    return arr, (cx, cy)


def simulate_filled_page(
    page: int, font_file, seed: int, pages_total: int = 3, name: str = "Demo"
) -> tuple[Image.Image, dict]:
    """Return the clean filled page (RGB) and ground truth ink boxes (mm)."""
    rng = np.random.default_rng(seed)
    pdf = build_template_pdf(pages_total)
    page_img = rasterize_pdf(pdf, SIM_PPM, [page])[0]
    paper = np.asarray(page_img, np.float32) / 255.0

    target_xh_mm = rng.uniform(4.0, 4.8)
    size_px = int(round(target_xh_mm * SIM_PPM / _x_height_ratio(font_file)))
    font = load_font(font_file, size_px)
    ink_rgb = np.array([0.10, 0.16, 0.48]) + rng.normal(0, 0.03, 3)
    coverage = np.zeros(paper.shape[:2], np.float32)
    truth = {}
    for slot in layout.slots():
        arr, (cx, cy) = _render_char(slot.char, font, rng)
        ys, xs = np.nonzero(arr > 0.35)
        if len(xs) == 0:
            continue
        writable = (slot.w - 2 * layout.CROP_INSET - 1.2) * SIM_PPM
        if xs.max() + 1 - xs.min() > writable:  # people write wide letters narrower in a box
            f = writable / (xs.max() + 1 - xs.min())
            arr = cv2.resize(arr, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
            cx, cy = cx * f, cy * f
            ys, xs = np.nonzero(arr > 0.35)
        gx0, gx1 = xs.min(), xs.max() + 1
        # centre horizontally in the box (with jitter), baseline near the guide
        box_cx = (slot.x + slot.w / 2 + rng.normal(0, 0.5)) * SIM_PPM
        base_y = (slot.baseline_y + rng.normal(0, 0.35)) * SIM_PPM
        ox = int(round(box_cx - (gx0 + gx1) / 2))
        oy = int(round(base_y - cy))
        # keep the glyph inside the writable part of the box
        lim_l = (slot.x + layout.CROP_INSET + 0.4) * SIM_PPM
        lim_r = (slot.x + slot.w - layout.CROP_INSET - 0.4) * SIM_PPM
        lim_t = (slot.y + layout.CROP_INSET + 0.4) * SIM_PPM
        lim_b = (slot.y + slot.h - layout.CROP_INSET - 0.4) * SIM_PPM
        ox += int(max(0, lim_l - (gx0 + ox)) - max(0, (gx1 + ox) - lim_r))
        oy += int(max(0, lim_t - (ys.min() + oy)) - max(0, (ys.max() + 1 + oy) - lim_b))
        h, w = arr.shape
        y0, x0 = oy, ox
        region = coverage[y0 : y0 + h, x0 : x0 + w]
        region[:] = np.maximum(region, arr[: region.shape[0], : region.shape[1]])
        truth[slot.char] = [
            float((xs.min() + ox) / SIM_PPM), float((ys.min() + oy) / SIM_PPM),
            float((xs.max() + 1 + ox) / SIM_PPM), float((ys.max() + 1 + oy) / SIM_PPM),
        ]

    # name field
    name_font = load_font(font_file, int(size_px * 0.8))
    name_layer = Image.new("L", page_img.size, 0)
    ImageDraw.Draw(name_layer).text((40 * SIM_PPM, 22 * SIM_PPM), name, fill=255, font=name_font, anchor="ls")
    coverage = np.maximum(coverage, np.asarray(name_layer, np.float32) / 255.0)

    a = np.clip(coverage, 0, 1)[..., None] * 0.92
    out = paper * (1 - a) + (paper * ink_rgb) * a
    return Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8)), truth


def simulate_photo(clean: Image.Image, seed: int, rotate90: bool = False) -> Image.Image:
    """Turn a clean page into something that looks like a phone photo of it."""
    rng = np.random.default_rng(seed)
    page = np.asarray(clean, np.float32) / 255.0
    h, w = page.shape[:2]
    W, H = int(w * 1.25), int(h * 1.18)

    # table background
    table = np.ones((H, W, 3), np.float32) * np.array([0.42, 0.36, 0.30])
    table += cv2.GaussianBlur(rng.normal(0, 0.05, (H, W, 1)).astype(np.float32), (0, 0), 3)[..., None]

    # perspective: page corners moved randomly
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    off = np.float32([(W - w) / 2, (H - h) / 2])
    jitter = rng.uniform(-0.045, 0.045, (4, 2)) * np.float32([w, h])
    dst = (src + off + jitter).astype(np.float32)
    M = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(page, M, (W, H), flags=cv2.INTER_LINEAR)
    mask = cv2.warpPerspective(np.ones((h, w), np.float32), M, (W, H))[..., None]
    img = warped * mask + table * (1 - mask)

    # gentle "bulge" of the paper (non-linear distortion the homography can't model)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    amp = rng.uniform(2.0, 5.0)
    map_x = xx + amp * np.sin(yy / H * np.pi) * np.sin(xx / W * np.pi)
    map_y = yy + amp * 0.6 * np.sin(xx / W * np.pi * 2)
    img = cv2.remap(img, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)

    # uneven lighting + colour cast + shadow
    gx, gy = rng.uniform(-0.25, 0.25, 2)
    light = 0.95 + gx * (xx / W - 0.5) + gy * (yy / H - 0.5)
    light -= 0.18 * np.exp(-(((xx - W * rng.uniform(0.6, 1.0)) / (W * 0.25)) ** 2))  # soft shadow
    img = img * light[..., None] * np.array([1.0, 0.96, 0.88])  # warm indoor light (RGB)
    img = cv2.GaussianBlur(img, (0, 0), rng.uniform(0.5, 0.9))
    img += rng.normal(0, 0.018, img.shape).astype(np.float32)
    out = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8))
    if rotate90:
        out = out.rotate(90, expand=True)
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=82)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def make_demo_inputs(input_dir: Path, font_file, pages: int = 2, seed: int = 7, name: str = "Demo") -> dict:
    """Write simulated photos into ``input_dir``. Returns ground truth per page."""
    input_dir.mkdir(parents=True, exist_ok=True)
    truth_all = {}
    for page in range(pages):
        clean, truth = simulate_filled_page(page, font_file, seed + 101 * page, name=name)
        photo = simulate_photo(clean, seed + 7 * page + 1, rotate90=(page == 1))
        photo_path = input_dir / f"foto_seite{page + 1}.jpg"
        photo.save(photo_path, quality=90)
        clean.save(input_dir.parent / f"scan_seite{page + 1}.png")
        truth_all[page] = truth
        log.info("Simuliertes Foto geschrieben: %s", photo_path)
    (input_dir.parent / "ground_truth.json").write_text(json.dumps(truth_all, ensure_ascii=False, indent=1), encoding="utf-8")
    return truth_all


def bbox_iou(a, b) -> float:
    ix0, iy0, ix1, iy1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0
