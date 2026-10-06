"""DIN-A4 writing paper backgrounds (lined, squared, blank) drawn as vectors."""

from __future__ import annotations

from dataclasses import dataclass

from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas

PAGE_W = 210.0
PAGE_H = 297.0

PAPER_STYLES = ("liniert", "kariert", "blanko")
LINE_COLOR = (0.55, 0.68, 0.84)
GRID_COLOR = (0.62, 0.72, 0.84)
MARGIN_COLOR = (0.86, 0.38, 0.38)
SQUARE_MM = 5.0


@dataclass
class PaperSpec:
    style: str = "liniert"
    line_spacing: float = 8.5  # mm between writing lines
    margin_left: float = 20.0
    margin_right: float = 15.0
    margin_top: float = 15.0
    margin_bottom: float = 15.0
    margin_lines: bool = True  # draw the red vertical margin lines

    def __post_init__(self) -> None:
        if self.style not in PAPER_STYLES:
            raise ValueError(f"Papier muss eines von {', '.join(PAPER_STYLES)} sein")
        if self.style == "kariert":
            # writing lines must fall on grid lines
            self.line_spacing = max(SQUARE_MM, round(self.line_spacing / SQUARE_MM) * SQUARE_MM)
        if not 4.0 <= self.line_spacing <= 20.0:
            raise ValueError("Zeilenabstand muss zwischen 4 und 20 mm liegen")

    def writing_lines(self) -> list[float]:
        """y positions (mm from top) of the baselines text is written on."""
        if self.style == "kariert":
            first = _grid_origin_y(self) + self.line_spacing
            while first < self.margin_top + self.line_spacing * 0.9:
                first += SQUARE_MM
        else:
            first = self.margin_top + self.line_spacing
        ys = []
        y = first
        while y <= PAGE_H - self.margin_bottom + 1e-6:
            ys.append(y)
            y += self.line_spacing
        return ys

    @property
    def text_left(self) -> float:
        return self.margin_left + 1.5

    @property
    def text_right(self) -> float:
        return PAGE_W - self.margin_right - 1.0


def _grid_origin_y(spec: PaperSpec) -> float:
    return spec.margin_top


def _pt(x: float, y: float) -> tuple[float, float]:
    return x * mm, (PAGE_H - y) * mm


def draw_paper(c: Canvas, spec: PaperSpec) -> None:
    c.saveState()
    if spec.style == "liniert":
        c.setStrokeColorRGB(*LINE_COLOR)
        c.setLineWidth(0.45)
        for y in spec.writing_lines():
            c.line(*_pt(0, y), *_pt(PAGE_W, y))
    elif spec.style == "kariert":
        c.setStrokeColorRGB(*GRID_COLOR)
        c.setLineWidth(0.3)
        top, bottom = _grid_origin_y(spec), PAGE_H - spec.margin_bottom
        x0 = 5.0
        y = top
        while y <= bottom + 1e-6:
            c.line(*_pt(x0, y), *_pt(PAGE_W - 5.0, y))
            y += SQUARE_MM
        x = x0
        while x <= PAGE_W - 5.0 + 1e-6:
            c.line(*_pt(x, top), *_pt(x, bottom))
            x += SQUARE_MM
    if spec.margin_lines and spec.style != "blanko":
        c.setStrokeColorRGB(*MARGIN_COLOR)
        c.setLineWidth(0.6)
        c.line(*_pt(spec.margin_left, 0), *_pt(spec.margin_left, PAGE_H))
        c.line(*_pt(PAGE_W - spec.margin_right, 0), *_pt(PAGE_W - spec.margin_right, PAGE_H))
    c.restoreState()
