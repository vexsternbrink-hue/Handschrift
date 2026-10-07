"""Render text in a learned handwriting onto DIN-A4 paper and save it as PDF."""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from .charset import normalize_text
from .config import HandschriftError
from .glyphs import GlyphRecord, GlyphSet
from .paper import PAGE_H, PAGE_W, PaperSpec, draw_paper

log = logging.getLogger(__name__)

INK_COLORS = {
    "blau": (0.10, 0.20, 0.58),
    "koenigsblau": (0.12, 0.25, 0.70),
    "dunkelblau": (0.08, 0.12, 0.38),
    "schwarz": (0.07, 0.07, 0.09),
    "rot": (0.75, 0.10, 0.12),
    "gruen": (0.05, 0.42, 0.20),
}
DEFAULT_LINE_SPACING = {"liniert": 8.5, "kariert": 10.0, "blanko": 8.5}
BASE_X_HEIGHT_MM = 2.9
MIN_X_HEIGHT_MM, MAX_X_HEIGHT_MM = 2.5, 3.5
CAP_RATIO = 0.64  # max. capital height relative to the line spacing
KERN_STEP_MM = 0.08


def parse_ink(value: str) -> tuple[float, float, float]:
    v = (value or "blau").strip().lower().replace("ü", "ue").replace("ö", "oe")
    if v in INK_COLORS:
        return INK_COLORS[v]
    m = re.fullmatch(r"#?([0-9a-f]{6})", v)
    if m:
        h = m.group(1)
        return tuple(int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]
    raise HandschriftError(f"Unbekannte Tintenfarbe '{value}'. Möglich: {', '.join(INK_COLORS)} oder #RRGGBB")


@dataclass
class RenderOptions:
    paper: str = "liniert"
    line_spacing: float | None = None  # mm; default depends on paper
    ink: str = "blau"
    size: float = 1.0  # multiplies the writing size
    jitter: float = 1.0  # 0 = perfectly regular, 1 = natural, 2 = sloppy
    seed: int | None = None
    margin_lines: bool = True
    preview: bool = False  # also write PNG previews next to the PDF
    title: str = "Handschrift"


@dataclass
class RenderResult:
    pdf: Path
    pages: int
    lines: int
    glyphs: int
    missing: dict[str, int] = field(default_factory=dict)
    previews: list[Path] = field(default_factory=list)


@dataclass
class _Glyph:
    rec: GlyphRecord
    x: float  # ink left, mm relative to word start
    k: float  # mm per glyph pixel
    dy: float  # baseline shift, mm (positive = down)
    angle: float  # degrees
    opacity: float


@dataclass
class _Word:
    glyphs: list[_Glyph]
    width: float


class Renderer:
    def __init__(self, glyphs: GlyphSet, options: RenderOptions):
        self.gs = glyphs
        self.opt = options
        spacing = options.line_spacing or DEFAULT_LINE_SPACING.get(options.paper, 8.5)
        self.paper = PaperSpec(style=options.paper, line_spacing=spacing, margin_lines=options.margin_lines)
        self.ink = parse_ink(options.ink)
        self.rng = np.random.default_rng(options.seed)
        self.j = max(0.0, float(options.jitter))
        if not 0.3 <= options.size <= 3.0:
            raise HandschriftError("--size muss zwischen 0.3 und 3.0 liegen")
        spacing = self.paper.line_spacing
        # writing size: a natural x-height, but capitals must leave room for
        # the descenders of the line above
        # the person's own writing size (measured on the template), within what
        # looks right on school paper
        natural = glyphs.x_height_px / glyphs.px_per_mm
        xh_mm = min(MAX_X_HEIGHT_MM, max(MIN_X_HEIGHT_MM, natural or BASE_X_HEIGHT_MM))
        k = min(xh_mm / glyphs.x_height_px, CAP_RATIO * spacing / glyphs.cap_height_px)
        k = min(k * options.size, 0.45 * spacing / glyphs.x_height_px)
        self.k = k  # mm per glyph pixel
        self.x_height = glyphs.x_height_px * k
        self._profiles: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self.missing: dict[str, int] = {}
        self._readers: dict[str, ImageReader] = {}
        self._last_variant: dict[str, int] = {}

    # ---------------------------------------------------------- planning
    def _n(self, sigma: float) -> float:
        return float(self.rng.normal(0.0, sigma * self.j)) if self.j > 0 else 0.0

    def _pick(self, ch: str) -> GlyphRecord:
        variants = self.gs.variants(ch)
        if len(variants) == 1:
            return variants[0]
        last = self._last_variant.get(ch)
        choices = [v for v in variants if v.variant != last] or variants
        rec = choices[int(self.rng.integers(len(choices)))]
        self._last_variant[ch] = rec.variant
        return rec

    def plan_word(self, word: str) -> _Word:
        glyphs: list[_Glyph] = []
        x = 0.0
        for ch in word:
            sub = self.gs.resolve(ch)
            if sub is None:
                self.missing[ch] = self.missing.get(ch, 0) + 1
                x += self.x_height * 0.6
                continue
            if sub != ch:
                self.missing[ch] = self.missing.get(ch, 0) + 1
            for c in sub:
                rec = self._pick(c)
                k = self.k * rec.scale * (1.0 + max(-0.08, min(0.08, self._n(0.03))))
                if glyphs:
                    x = self._next_x(glyphs[-1], rec, k, x)
                glyphs.append(_Glyph(rec, x, k, self._n(0.07), self._n(1.2), 1.0 - abs(self._n(0.05))))
                x = x + rec.ink_width * k
        return _Word(glyphs, x)

    # Optical letter spacing: instead of putting bounding boxes side by side,
    # move the next letter as close as its actual outline allows (like a
    # person writing), keeping a small natural gap.
    def _profile(self, rec: GlyphRecord) -> tuple[np.ndarray, np.ndarray]:
        prof = self._profiles.get(rec.file)
        if prof is None:
            ink = np.asarray(self.gs.alpha(rec)) > 70
            has = ink.any(axis=1)
            left = np.where(has, ink.argmax(axis=1), np.nan).astype(float)
            right = np.where(has, ink.shape[1] - 1 - ink[:, ::-1].argmax(axis=1), np.nan).astype(float)
            prof = (left, right)
            self._profiles[rec.file] = prof
        return prof

    def _sample(self, rec: GlyphRecord, k: float, ys: np.ndarray, side: int) -> np.ndarray:
        prof = self._profile(rec)[side]
        rows = np.round(rec.baseline + ys / k).astype(int)
        out = np.full(ys.shape, np.nan)
        ok = (rows >= 0) & (rows < len(prof))
        out[ok] = (prof[rows[ok]] - rec.ink_left) * k
        return out

    def _next_x(self, prev: _Glyph, rec: GlyphRecord, k: float, x_after_prev: float) -> float:
        gap = self.x_height * max(0.05, 0.15 + self._n(0.035))
        bbox_x = x_after_prev + gap
        ys = np.arange(-4.0 * self.x_height, 2.0 * self.x_height, KERN_STEP_MM)
        right_a = prev.x + self._sample(prev.rec, prev.k, ys, 1)
        left_b = self._sample(rec, k, ys, 0)
        # look a little above/below as well so diagonal strokes don't touch
        reach = int(round(0.18 * self.x_height / KERN_STEP_MM))
        ra = np.where(np.isnan(right_a), -np.inf, right_a)
        lb = np.where(np.isnan(left_b), np.inf, left_b)
        ra_grown = ra.copy()
        for s_ in range(1, reach + 1):
            ra_grown[s_:] = np.maximum(ra_grown[s_:], ra[:-s_])
            ra_grown[:-s_] = np.maximum(ra_grown[:-s_], ra[s_:])
        need = ra_grown - lb
        need = need[np.isfinite(need)]
        if need.size == 0:
            return bbox_x
        x = float(need.max()) + gap
        # letters may tuck into each other like real handwriting; punctuation keeps its distance
        if not (prev.rec.char.isalnum() and rec.char.isalnum()):
            return max(x, x_after_prev + gap)
        # never tuck in by more than 40 % of the narrower letter
        min_w = min(prev.rec.ink_width * prev.k, rec.ink_width * k)
        return max(x, x_after_prev - 0.4 * min_w)

    def space_width(self) -> float:
        return self.x_height * (0.95 + self._n(0.12))

    def _split_long(self, word: str, available: float) -> list[_Word]:
        """Break a word that doesn't fit on one line into pieces that do."""
        parts, current = [], ""
        for ch in word:
            if current and self.plan_word(current + ch).width > available:
                parts.append(current)
                current = ch
            else:
                current += ch
        if current:
            parts.append(current)
        return [self.plan_word(p) for p in parts]

    def layout(self, text: str) -> list[list[tuple[float, _Word]]]:
        """Lines of (x offset in mm from the text start, word)."""
        available = self.paper.text_right - self.paper.text_left
        lines: list[list[tuple[float, _Word]]] = []
        for para in normalize_text(text).split("\n"):
            line: list[tuple[float, _Word]] = []
            x = 0.0
            pending_space = 0.0
            for token in re.split(r"( +)", para):
                if not token:
                    continue
                if token.startswith(" "):
                    pending_space += sum(self.space_width() for _ in token)
                    continue
                word = self.plan_word(token)
                pieces = [word] if word.width <= available else self._split_long(token, available)
                for piece in pieces:
                    # leading spaces of a paragraph indent its first line
                    start = x + pending_space if line else min(pending_space, max(0.0, available - piece.width))
                    if line and start + piece.width > available:
                        lines.append(line)
                        line, x, start = [], 0.0, 0.0
                    line.append((start, piece))
                    x = start + piece.width
                    pending_space = 0.0
                pending_space = 0.0
            lines.append(line)
        while lines and not lines[-1]:
            lines.pop()
        return lines

    # ----------------------------------------------------------- drawing
    def _reader(self, rec: GlyphRecord) -> ImageReader:
        r = self._readers.get(rec.file)
        if r is None:
            alpha = self.gs.alpha(rec)
            color = tuple(int(round(v * 255)) for v in self.ink)
            rgba = Image.new("RGBA", alpha.size, color + (0,))
            rgba.putalpha(alpha)
            r = ImageReader(rgba)
            self._readers[rec.file] = r
        return r

    def _draw_glyph(self, c: Canvas, g: _Glyph, x_mm: float, base_mm: float) -> None:
        rec = g.rec
        img_left = x_mm - rec.ink_left * g.k
        w, h = rec.width * g.k, rec.height * g.k
        c.saveState()
        c.setFillAlpha(g.opacity)
        c.translate(img_left * mm, (PAGE_H - base_mm) * mm)
        if g.angle:
            c.rotate(-g.angle)
        c.drawImage(self._reader(rec), 0, -(rec.height - rec.baseline) * g.k * mm, w * mm, h * mm, mask="auto")
        c.restoreState()

    def render(self, text: str, out_pdf: Path) -> RenderResult:
        lines = self.layout(text)
        rows = self.paper.writing_lines()
        if not rows:
            raise HandschriftError("Auf dem Papier ist keine Zeile frei (Ränder/Zeilenabstand prüfen).")
        out_pdf = Path(out_pdf)
        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        c = Canvas(str(out_pdf), pagesize=(PAGE_W * mm, PAGE_H * mm))
        c.setTitle(self.opt.title)
        c.setCreator("Handschrift-Generator")
        pages = max(1, math.ceil(len(lines) / len(rows)))
        glyph_count = 0
        for p in range(pages):
            draw_paper(c, self.paper)
            for row_idx, line in enumerate(lines[p * len(rows) : (p + 1) * len(rows)]):
                if not line:
                    continue
                base = rows[row_idx] + self._n(0.12)
                slope = math.tan(math.radians(self._n(0.35)))
                x0 = self.paper.text_left + abs(self._n(0.5))
                for wx, word in line:
                    word_dy = self._n(0.15)
                    for g in word.glyphs:
                        x = x0 + wx + g.x
                        self._draw_glyph(c, g, x, base + word_dy + g.dy + (x - x0) * slope)
                        glyph_count += 1
            c.showPage()
        c.save()

        result = RenderResult(out_pdf, pages, len(lines), glyph_count, dict(self.missing))
        if self.missing:
            log.warning(
                "Nicht gelernte Zeichen (ersetzt oder ausgelassen): %s",
                ", ".join(f"'{ch}'×{n}" for ch, n in sorted(self.missing.items())),
            )
        if self.opt.preview:
            result.previews = write_previews(out_pdf)
        log.info("PDF geschrieben: %s (%d Seite(n), %d Zeilen, %d Zeichen)", out_pdf, pages, len(lines), glyph_count)
        return result


def write_previews(pdf: Path, px_per_mm: float = 5.0) -> list[Path]:
    from .template import rasterize_pdf

    paths = []
    for i, img in enumerate(rasterize_pdf(Path(pdf), px_per_mm)):
        p = pdf.with_name(f"{pdf.stem}_seite{i + 1}.png")
        img.save(p)
        paths.append(p)
    return paths


def render_text(glyphs: GlyphSet, text: str, out_pdf: Path, options: RenderOptions | None = None) -> RenderResult:
    if not text.strip():
        raise HandschriftError("Der Text ist leer.")
    return Renderer(glyphs, options or RenderOptions()).render(text, out_pdf)
