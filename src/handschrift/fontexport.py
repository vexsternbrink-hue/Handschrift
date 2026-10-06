"""Export a learned handwriting as a TrueType font (.ttf).

The PDF renderer works directly with the glyph images (which keeps pen texture
and several variants per letter). The font is a bonus so the handwriting can
also be used in Word, LibreOffice, etc. It uses the first variant of each
character, traced into outlines with OpenCV.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from .config import HandschriftError
from .glyphs import GlyphSet

log = logging.getLogger(__name__)

UPM = 1000


def _glyph_name(ch: str) -> str:
    return f"uni{ord(ch):04X}"


def _signed_area(pts: np.ndarray) -> float:
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _trace(gs: GlyphSet, rec, units_per_px: float, lsb: float):
    alpha = np.asarray(gs.alpha(rec))
    mask = (alpha > 110).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8))
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    pen = TTGlyphPen(None)
    if hierarchy is None:
        return pen.glyph()
    for cnt, h in zip(contours, hierarchy[0]):
        if len(cnt) < 4:
            continue
        approx = cv2.approxPolyDP(cnt, 0.7, True).reshape(-1, 2).astype(float)
        if len(approx) < 3:
            continue
        pts = np.empty_like(approx)
        pts[:, 0] = (approx[:, 0] - rec.ink_left) * units_per_px + lsb
        pts[:, 1] = (rec.baseline - approx[:, 1]) * units_per_px
        is_hole = h[3] != -1
        area = _signed_area(pts)
        if abs(area) < 4:
            continue
        # TrueType: outer contours clockwise (negative area), holes counter-clockwise
        if (not is_hole and area > 0) or (is_hole and area < 0):
            pts = pts[::-1]
        pts = np.round(pts).astype(int)
        pen.moveTo(tuple(pts[0]))
        for p in pts[1:]:
            pen.lineTo(tuple(p))
        pen.closePath()
    return pen.glyph()


def export_font(gs: GlyphSet, out_path: Path, family: str) -> Path:
    if not gs.glyphs:
        raise HandschriftError("Keine Zeichen vorhanden – erst trainieren.")
    cap_units = 700.0
    units_per_px = cap_units / gs.cap_height_px
    side = round(0.09 * gs.x_height_px * units_per_px)

    order = [".notdef", "space"]
    cmap = {32: "space"}
    glyphs = {}
    metrics = {}
    pen = TTGlyphPen(None)
    pen.moveTo((50, 0)); pen.lineTo((50, 700)); pen.lineTo((450, 700)); pen.lineTo((450, 0)); pen.closePath()
    glyphs[".notdef"] = pen.glyph()
    metrics[".notdef"] = (500, 50)
    glyphs["space"] = TTGlyphPen(None).glyph()
    space = round(gs.x_height_px * units_per_px * 0.95)
    metrics["space"] = (space, 0)

    for ch, recs in gs.glyphs.items():
        if len(ch) != 1:
            continue
        rec = recs[0]
        name = _glyph_name(ch)
        glyphs[name] = _trace(gs, rec, units_per_px, side)
        metrics[name] = (round(rec.ink_width * units_per_px + 2 * side), side)
        cmap[ord(ch)] = name
        order.append(name)

    ascent = round(max(r.baseline for recs in gs.glyphs.values() for r in recs) * units_per_px * 1.05)
    descent = round(max(r.height - r.baseline for recs in gs.glyphs.values() for r in recs) * units_per_px * 1.05)
    ascent, descent = max(ascent, 800), max(descent, 200)

    fb = FontBuilder(UPM, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyphs)
    # recompute bounding boxes / left side bearings from the outlines
    glyf = fb.font["glyf"]
    hmtx = {}
    for name in order:
        g = glyf[name]
        g.recalcBounds(glyf)
        lsb = getattr(g, "xMin", 0) if g.numberOfContours else 0
        hmtx[name] = (metrics[name][0], lsb)
    fb.setupHorizontalMetrics(hmtx)
    fb.setupHorizontalHeader(ascent=ascent, descent=-descent)
    ps_name = "".join(c for c in family if c.isalnum()) or "Handschrift"
    fb.setupNameTable({"familyName": family, "styleName": "Regular", "psName": f"{ps_name}-Regular"})
    fb.setupOS2(
        sTypoAscender=ascent, sTypoDescender=-descent, usWinAscent=ascent, usWinDescent=descent,
        sxHeight=round(gs.x_height_px * units_per_px), sCapHeight=round(cap_units),
    )
    fb.setupPost()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fb.save(str(out_path))
    log.info("Schriftart exportiert: %s (%d Zeichen)", out_path, len(cmap) - 1)
    return out_path
