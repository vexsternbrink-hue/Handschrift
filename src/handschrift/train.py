"""Training: read photos/scans of the filled template and cut out every character.

Pipeline per photo:
  1. find the ArUco corner markers -> which template page, and a homography
  2. rectify the page to a flat image at GLYPH_PX_PER_MM
  3. flatten the lighting (divide by a background estimate)
  4. for every box: snap to the printed box outline, cut out the inside,
     separate pen ink from paper/guide lines, store the glyph as an alpha PNG
Finally the per-glyph baselines and the writing size are computed and
everything is written to ``users/<id>/handwriting``.
"""

from __future__ import annotations

import logging
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import layout
from .charset import BASELINE_CHARS, CAPHEIGHT_CHARS, CHARSET, XHEIGHT_CHARS, glyph_filename
from .config import GLYPH_PX_PER_MM, HandschriftError
from .glyphs import GlyphRecord, GlyphSet, overview_sheet
from .imageio import expand_inputs, load_pages
from .template import aruco_dictionary
from .users import UserProfile, now_iso, write_json_atomic

log = logging.getLogger(__name__)

PPM = GLYPH_PX_PER_MM

# Normalised brightness (paper = 1.0) below which a pixel counts as ink.
INK_THRESHOLD = 0.66
# A component whose darkest pixel is lighter than this is printed guide line, not pen.
FAINT_LEVEL = 0.52
ALPHA_WHITE = 0.90  # brightness that maps to alpha 0
ALPHA_BLACK = 0.30  # brightness that maps to alpha 1
MIN_COMPONENT_PX = 10
MIN_GLYPH_INK_MM2 = 0.35
SNAP_SEARCH_MM = 3.0


@dataclass
class PageDetection:
    page: int
    homography: np.ndarray  # source image px -> rectified px
    markers_found: int
    reprojection_error_mm: float


@dataclass
class ExtractedGlyph:
    char: str
    alpha: np.ndarray  # uint8, 255 = ink
    template_baseline: float  # px from the top of ``alpha``
    ink_box: tuple[int, int, int, int]  # left, top, right(excl), bottom(excl) inside alpha
    page_bbox_mm: list[float]
    source: str


@dataclass
class PageResult:
    source: str
    page: int
    glyphs: list[ExtractedGlyph]
    empty: list[str]
    snapped: int
    reprojection_error_mm: float
    debug_image: Path | None = None


@dataclass
class TrainReport:
    user_id: str
    pages: list[PageResult] = field(default_factory=list)
    failed_inputs: list[tuple[str, str]] = field(default_factory=list)
    chars_found: list[str] = field(default_factory=list)
    chars_missing: list[str] = field(default_factory=list)
    variants_total: int = 0
    overview: Path | None = None

    def summary(self) -> str:
        lines = [
            f"Nutzer: {self.user_id}",
            f"Verarbeitete Seiten: {len(self.pages)}  (fehlgeschlagen: {len(self.failed_inputs)})",
            f"Gelernte Zeichen: {len(self.chars_found)}/{len(CHARSET)}  (Varianten insgesamt: {self.variants_total})",
        ]
        if self.chars_missing:
            lines.append("Fehlende Zeichen: " + " ".join(self.chars_missing))
        for src, err in self.failed_inputs:
            lines.append(f"FEHLER {src}: {err}")
        if self.overview:
            lines.append(f"Kontrollbild: {self.overview}")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# 1. marker detection + rectification
# --------------------------------------------------------------------------

def _detector():
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    params.adaptiveThreshWinSizeMax = 53
    return cv2.aruco.ArucoDetector(aruco_dictionary(), params)


def detect_page(img_bgr: np.ndarray) -> PageDetection:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    detector = _detector()
    best: dict[int, np.ndarray] = {}
    attempts = []
    long_side = max(gray.shape)
    for target in (2400, 3600, long_side):
        scale = min(1.0, target / long_side)
        g = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray
        for variant in (g, cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(g)):
            attempts.append(scale)
            corners, ids, _ = detector.detectMarkers(variant)
            found = {}
            if ids is not None:
                for c, i in zip(corners, ids.ravel()):
                    if int(i) < 4 * layout.MAX_PAGES:
                        found[int(i)] = c.reshape(4, 2) / scale
            if len(found) > len(best):
                best = found
            if _markers_for_best_page(best)[1] >= 4:
                break
        if _markers_for_best_page(best)[1] >= 4:
            break

    page, count = _markers_for_best_page(best)
    if count < 3:
        raise HandschriftError(
            f"Nur {count} von 4 Ecken-Markern erkannt. Bitte das ganze Blatt mit allen vier schwarzen "
            "Ecken-Quadraten fotografieren (gerade von oben, gut beleuchtet, scharf)."
        )
    src, dst = [], []
    for mid, pts in best.items():
        if layout.page_of_marker(mid) == page:
            src.append(pts)
            dst.append(layout.marker_corners_mm(mid) * PPM)
    src_a = np.concatenate(src).astype(np.float64)
    dst_a = np.concatenate(dst).astype(np.float64)
    H, _ = cv2.findHomography(src_a, dst_a, 0)
    if H is None:
        raise HandschriftError("Perspektive konnte nicht berechnet werden (Marker unplausibel).")
    proj = cv2.perspectiveTransform(src_a.reshape(-1, 1, 2), H).reshape(-1, 2)
    err_mm = float(np.sqrt(((proj - dst_a) ** 2).sum(axis=1)).mean() / PPM)
    return PageDetection(page=page, homography=H, markers_found=count, reprojection_error_mm=err_mm)


def _markers_for_best_page(found: dict[int, np.ndarray]) -> tuple[int, int]:
    counts: dict[int, int] = defaultdict(int)
    for mid in found:
        counts[layout.page_of_marker(mid)] += 1
    if not counts:
        return 0, 0
    page = max(counts, key=lambda p: counts[p])
    return page, counts[page]


def rectify(img_bgr: np.ndarray, H: np.ndarray) -> np.ndarray:
    size = (int(round(layout.PAGE_W * PPM)), int(round(layout.PAGE_H * PPM)))
    # When the photo has much higher resolution than the target, shrink it
    # first so thin strokes don't alias.
    lin = H[:2, :2] / H[2, 2]
    scale = float(np.sqrt(abs(np.linalg.det(lin))))
    if scale < 0.7:
        f = scale / 0.9
        img_bgr = cv2.resize(img_bgr, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        H = H @ np.diag([1.0 / f, 1.0 / f, 1.0])
    return cv2.warpPerspective(
        img_bgr, H, size, flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255)
    )


def flatten_lighting(img_bgr: np.ndarray) -> np.ndarray:
    """Brightness relative to the local paper colour: paper ~1.0, ink << 1."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))
    bg = cv2.dilate(gray, k)
    bg = cv2.GaussianBlur(bg, (0, 0), 12)
    return np.clip(gray / np.maximum(bg, 0.05), 0.0, 1.0)


# --------------------------------------------------------------------------
# 2. per-box extraction
# --------------------------------------------------------------------------

def _outline_template(w: int, h: int, thickness: int = 3) -> np.ndarray:
    t = np.zeros((h + thickness, w + thickness), np.float32)
    o = thickness // 2
    cv2.rectangle(t, (o, o), (o + w, o + h), 1.0, thickness)
    return t


def snap_box(dark: np.ndarray, slot: layout.Slot) -> tuple[float, float, bool]:
    """Find the printed outline of ``slot`` near its expected place.

    Returns the (dx, dy) correction in px and whether the outline was found.
    Paper that isn't perfectly flat bends the grid a little; this keeps every
    box aligned anyway.
    """
    m = int(SNAP_SEARCH_MM * PPM)
    w, h = int(round(slot.w * PPM)), int(round(slot.h * PPM))
    tmpl = _outline_template(w, h)
    th, tw = tmpl.shape
    x0 = int(round(slot.x * PPM)) - (tw - w) // 2 - m
    y0 = int(round(slot.y * PPM)) - (th - h) // 2 - m
    H, W = dark.shape
    if x0 < 0 or y0 < 0 or x0 + tw + 2 * m > W or y0 + th + 2 * m > H:
        return 0.0, 0.0, False
    region = dark[y0 : y0 + th + 2 * m, x0 : x0 + tw + 2 * m]
    res = cv2.matchTemplate(region, tmpl, cv2.TM_CCORR)
    _, best, _, loc = cv2.minMaxLoc(res)
    expected = float(tmpl.sum()) / 3.0  # one pixel-line of the outline
    if best < 0.45 * expected:
        return 0.0, 0.0, False
    return float(loc[0] - m), float(loc[1] - m), True


def extract_box(norm: np.ndarray, slot: layout.Slot, dx: float, dy: float, source: str) -> ExtractedGlyph | None:
    inset = layout.CROP_INSET * PPM
    left = int(round(slot.x * PPM + dx + inset))
    top = int(round(slot.y * PPM + dy + inset))
    right = int(round((slot.x + slot.w) * PPM + dx - inset))
    bottom = int(round((slot.y + slot.h) * PPM + dy - inset))
    crop = norm[top:bottom, left:right]
    if crop.size == 0:
        return None

    ink = (crop < INK_THRESHOLD).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    keep = np.zeros(n, bool)
    ch, cw = crop.shape
    thin = 0.7 * PPM
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < MIN_COMPONENT_PX:
            continue
        comp_min = float(crop[labels == i].min())
        if comp_min > FAINT_LEVEL and h <= thin:
            continue  # light printed guide-line dots
        touches = x == 0 or y == 0 or x + w >= cw or y + h >= ch
        if touches and (w <= thin or h <= thin) and max(w, h) > 3 * PPM:
            continue  # remainder of the printed box outline
        keep[i] = True
    mask = keep[labels]
    if mask.sum() < MIN_GLYPH_INK_MM2 * PPM * PPM:
        return None

    alpha = np.clip((ALPHA_WHITE - crop) / (ALPHA_WHITE - ALPHA_BLACK), 0.0, 1.0)
    grown = cv2.dilate(mask.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))) > 0
    alpha = alpha * grown
    p95 = float(np.percentile(alpha[mask], 95))
    alpha = np.clip(alpha / max(p95, 0.35), 0.0, 1.0)

    ys, xs = np.nonzero(mask)
    pad = 3
    ix0, ix1 = max(xs.min() - pad, 0), min(xs.max() + 1 + pad, cw)
    iy0, iy1 = max(ys.min() - pad, 0), min(ys.max() + 1 + pad, ch)
    glyph = (alpha[iy0:iy1, ix0:ix1] * 255).round().astype(np.uint8)
    tmpl_baseline = slot.baseline_y * PPM + dy - (top + iy0)
    ink_box = (int(xs.min() - ix0), int(ys.min() - iy0), int(xs.max() + 1 - ix0), int(ys.max() + 1 - iy0))
    bbox_mm = [
        float((left + xs.min()) / PPM),
        float((top + ys.min()) / PPM),
        float((left + xs.max() + 1) / PPM),
        float((top + ys.max() + 1) / PPM),
    ]
    return ExtractedGlyph(slot.char, glyph, float(tmpl_baseline), ink_box, bbox_mm, source)


def process_page_image(img_bgr: np.ndarray, source: str, debug_dir: Path | None = None) -> PageResult:
    det = detect_page(img_bgr)
    if det.reprojection_error_mm > 1.5:
        log.warning("%s: Marker passen schlecht zusammen (Fehler %.1f mm) – Ergebnis prüfen.", source, det.reprojection_error_mm)
    flat = rectify(img_bgr, det.homography)
    norm = flatten_lighting(flat)
    dark = (norm < 0.8).astype(np.float32)

    glyphs, empty, snapped = [], [], 0
    boxes = []
    for slot in layout.slots():
        dx, dy, ok = snap_box(dark, slot)
        snapped += ok
        g = extract_box(norm, slot, dx, dy, f"{source}#S{det.page + 1}")
        boxes.append((slot, dx, dy, g is not None))
        if g is None:
            empty.append(slot.char)
        else:
            glyphs.append(g)

    result = PageResult(source, det.page, glyphs, empty, snapped, det.reprojection_error_mm)
    if debug_dir is not None:
        result.debug_image = _write_debug(flat, boxes, debug_dir / f"{source}_seite{det.page + 1}.jpg")
    log.info(
        "%s: Seite %d erkannt (%d Marker, Fehler %.2f mm), %d Zeichen gefunden, %d leer, %d/%d Kästchen eingerastet",
        source, det.page + 1, det.markers_found, det.reprojection_error_mm, len(glyphs), len(empty), snapped, len(boxes),
    )
    return result


def _write_debug(flat: np.ndarray, boxes, path: Path) -> Path:
    s = 0.4
    img = cv2.resize(flat, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    for slot, dx, dy, found in boxes:
        p0 = (int((slot.x * PPM + dx) * s), int((slot.y * PPM + dy) * s))
        p1 = (int(((slot.x + slot.w) * PPM + dx) * s), int(((slot.y + slot.h) * PPM + dy) * s))
        color = (40, 170, 40) if found else (40, 40, 220)
        cv2.rectangle(img, p0, p1, color, 2)
        by = int((slot.baseline_y * PPM + dy) * s)
        cv2.line(img, (p0[0], by), (p1[0], by), (230, 160, 40), 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return path


# --------------------------------------------------------------------------
# 3. assemble the glyph set
# --------------------------------------------------------------------------

def _baselines(records: list[GlyphRecord]) -> None:
    """Use the ink bottom as baseline for characters that sit on the line,
    and the printed guide (corrected by the user's typical offset) for the rest."""
    offsets = [r.ink_bottom - r.template_baseline for r in records if r.char in BASELINE_CHARS]
    offset = float(np.median(offsets)) if offsets else 0.0
    offset = float(np.clip(offset, -3 * PPM, 3 * PPM))
    for r in records:
        guide = r.template_baseline + offset
        if r.char in BASELINE_CHARS and abs(r.ink_bottom - guide) < 3.5 * PPM:
            r.baseline = float(r.ink_bottom)
        else:
            r.baseline = float(guide)


def _median_height(records: list[GlyphRecord], chars: str) -> float | None:
    hs = [r.baseline - r.ink_top for r in records if r.char in chars]
    return float(np.median(hs)) if hs else None


def train_user(
    profile: UserProfile, inputs: list[Path] | None = None, append: bool = False
) -> TrainReport:
    files = expand_inputs(inputs if inputs else [profile.input_dir])
    if not files:
        raise HandschriftError(
            f"Keine Bilder gefunden. Lege Fotos/Scans in {profile.input_dir} oder gib sie mit --input an."
        )
    report = TrainReport(profile.user_id)
    profile.ensure_dirs()
    debug_dir = profile.debug_dir
    for old in debug_dir.glob("*.jpg"):
        if not append:
            old.unlink()

    for f in files:
        try:
            pages = load_pages(f)
        except HandschriftError as exc:
            log.error("%s", exc)
            report.failed_inputs.append((f.name, str(exc)))
            continue
        for label, img in pages:
            try:
                report.pages.append(process_page_image(img, label, debug_dir))
            except HandschriftError as exc:
                log.error("%s: %s", label, exc)
                report.failed_inputs.append((label, str(exc)))
            except Exception as exc:  # keep going with the other photos
                log.exception("%s: unerwarteter Fehler", label)
                report.failed_inputs.append((label, f"{type(exc).__name__}: {exc}"))

    if not any(p.glyphs for p in report.pages):
        raise HandschriftError("Aus keinem Bild konnten Zeichen gelesen werden.\n" + report.summary())

    hw = profile.handwriting_dir
    existing: dict[str, list[GlyphRecord]] = {}
    sources: list[str] = []
    if append and (hw / "glyphs.json").is_file():
        old = GlyphSet.load(hw)
        existing, sources = old.glyphs, list(old.sources)

    staging = hw / "glyphs.new"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    if append and existing:
        for recs in existing.values():
            for r in recs:
                shutil.copy2(hw / "glyphs" / r.file, staging / r.file)

    records: dict[str, list[GlyphRecord]] = {ch: list(v) for ch, v in existing.items()}
    for page in report.pages:
        sources.append(f"{page.source} (Seite {page.page + 1})")
        for g in page.glyphs:
            recs = records.setdefault(g.char, [])
            variant = max((r.variant for r in recs), default=-1) + 1
            name = glyph_filename(g.char, variant)
            Image.fromarray(g.alpha, mode="L").save(staging / name, optimize=True)
            l, t, r_, b = g.ink_box
            recs.append(
                GlyphRecord(
                    char=g.char, variant=variant, file=name, width=g.alpha.shape[1], height=g.alpha.shape[0],
                    baseline=g.template_baseline, ink_left=l, ink_right=r_, ink_top=t, ink_bottom=b,
                    template_baseline=g.template_baseline, source=g.source, page_bbox_mm=g.page_bbox_mm,
                )
            )

    flat = [r for recs in records.values() for r in recs]
    _baselines(flat)
    xh = _median_height(flat, XHEIGHT_CHARS)
    cap = _median_height(flat, CAPHEIGHT_CHARS)
    if xh is None and cap is None:
        xh, cap = 5.0 * PPM, 8.0 * PPM
    elif xh is None:
        xh = cap * 0.62
    elif cap is None:
        cap = xh / 0.62

    ordered = {ch: records[ch] for ch in CHARSET if ch in records}
    ordered.update({ch: v for ch, v in records.items() if ch not in ordered})
    gs = GlyphSet(
        directory=hw, px_per_mm=PPM, x_height_px=xh, cap_height_px=cap, glyphs=ordered,
        created=now_iso(), sources=sources, missing=[c for c in CHARSET if c not in ordered],
    )

    # swap in the new glyph directory and metadata
    final = hw / "glyphs"
    backup = hw / "glyphs.old"
    if backup.exists():
        shutil.rmtree(backup)
    if final.exists():
        final.rename(backup)
    staging.rename(final)
    write_json_atomic(gs.json_path, gs.to_json())
    if backup.exists():
        shutil.rmtree(backup)

    profile.trained_at = gs.created
    profile.save()

    report.chars_found = list(ordered)
    report.chars_missing = gs.missing
    report.variants_total = len(flat)
    try:
        report.overview = overview_sheet(GlyphSet.load(hw), hw / "glyph_uebersicht.png")
    except Exception:  # the overview is a convenience only
        log.exception("Kontrollbild konnte nicht erstellt werden")
    log.info("Training abgeschlossen:\n%s", report.summary())
    return report
