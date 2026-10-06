"""Generates the printable fill-in template (PDF) and PNG previews of it."""

from __future__ import annotations

import io
import logging
from pathlib import Path

import cv2
import numpy as np
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas

from . import layout
from .layout import PAGE_H, PAGE_W

log = logging.getLogger(__name__)

ARUCO_DICT_ID = cv2.aruco.DICT_4X4_50

# Short hints for characters that are easy to confuse.
LABEL_HINTS = {
    "I": "groß i",
    "l": "klein L",
    "O": "Buchst.",
    "0": "Null",
    "-": "Strich",
    '"': "gerade",
    "'": "Apostr.",
    "„": "unten",
    "“": "oben",
    ",": "Komma",
    ".": "Punkt",
}


def aruco_dictionary():
    return cv2.aruco.getPredefinedDictionary(ARUCO_DICT_ID)


def marker_bits(marker_id: int) -> np.ndarray:
    """6x6 array (incl. black border), True = black cell."""
    img = cv2.aruco.generateImageMarker(aruco_dictionary(), marker_id, 6)
    return img < 128


def _pt(x_mm: float, y_mm: float) -> tuple[float, float]:
    """Top-left based millimetres -> reportlab points (bottom-left origin)."""
    return x_mm * mm, (PAGE_H - y_mm) * mm


def _draw_marker(c: rl_canvas.Canvas, marker_id: int) -> None:
    bits = marker_bits(marker_id)
    x0, y0 = layout.marker_origins()[marker_id % 4]
    cell = layout.MARKER_SIZE / bits.shape[0]
    c.setFillGray(0)
    for r in range(bits.shape[0]):
        for col in range(bits.shape[1]):
            if bits[r, col]:
                x, y = _pt(x0 + col * cell, y0 + (r + 1) * cell)
                # tiny overlap avoids hairline gaps between cells in some viewers
                c.rect(x, y, cell * mm + 0.05, cell * mm + 0.05, stroke=0, fill=1)


def _draw_page(c: rl_canvas.Canvas, page: int, pages: int) -> None:
    for marker_id in layout.marker_ids(page):
        _draw_marker(c, marker_id)

    # Header
    c.setFillGray(0)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(*_pt(28, 16), "Handschrift-Vorlage")
    c.setFont("Helvetica", 9)
    status = "Pflichtseite" if page == 0 else "optional – mehr Variation"
    c.drawRightString(*_pt(182, 16), f"Seite {page + 1} von {pages}  ({status})")
    c.drawString(*_pt(28, 22.5), "Name: ______________________")
    c.setFont("Helvetica", 7)
    c.setFillGray(0.25)
    c.drawString(*_pt(90, 22.5), "Jedes Zeichen 1x in sein Kästchen. Kleinbuchstaben zwischen den")
    c.drawString(*_pt(90, 25.5), "gepunkteten Linien, Großbuchstaben/Oberlängen darüber, Unterlängen darunter.")

    # Character boxes
    for slot in layout.slots():
        c.setStrokeGray(0.5)
        c.setLineWidth(0.6)
        c.setDash()
        x, y = _pt(slot.x, slot.y + slot.h)
        c.rect(x, y, slot.w * mm, slot.h * mm, stroke=1, fill=0)

        c.setLineCap(1)
        c.setStrokeGray(0.82)
        c.setLineWidth(0.6)
        c.setDash(0.5, 3.5)
        c.line(*_pt(slot.x + 1.0, slot.midline_y), *_pt(slot.x + slot.w - 1.0, slot.midline_y))
        c.setStrokeGray(0.74)
        c.setLineWidth(0.8)
        c.setDash(0.5, 2.6)
        c.line(*_pt(slot.x + 1.0, slot.baseline_y), *_pt(slot.x + slot.w - 1.0, slot.baseline_y))
        c.setDash()
        c.setLineCap(0)

        lx, ly = slot.label_pos
        c.setFillGray(0.1)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(*_pt(lx, ly), slot.char)
        hint = LABEL_HINTS.get(slot.char)
        if hint:
            c.setFillGray(0.45)
            c.setFont("Helvetica", 5.5)
            c.drawString(*_pt(lx + 3.2, ly), hint)

    # Footer
    c.setFillGray(0.25)
    c.setFont("Helvetica", 7)
    c.drawCentredString(*_pt(PAGE_W / 2, 279), "Dunkler Stift (schwarz/blau), nicht über die Kästchen hinaus schreiben.")
    c.drawCentredString(*_pt(PAGE_W / 2, 283), "Die vier schwarzen Ecken-Marker beim Fotografieren nicht verdecken oder abschneiden.")


def build_template_pdf(pages: int = 3) -> bytes:
    if not 1 <= pages <= layout.MAX_PAGES:
        raise ValueError(f"Seitenzahl muss zwischen 1 und {layout.MAX_PAGES} liegen")
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(PAGE_W * mm, PAGE_H * mm))
    c.setTitle("Handschrift-Vorlage")
    c.setAuthor("Handschrift-Generator")
    for page in range(pages):
        _draw_page(c, page, pages)
        c.showPage()
    c.save()
    return buf.getvalue()


def write_template(path: Path, pages: int = 3) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_template_pdf(pages))
    log.info("Template mit %d Seite(n) geschrieben: %s", pages, path)
    return path


def rasterize_pdf(pdf: bytes | Path, px_per_mm: float, page_indices: list[int] | None = None):
    """Render PDF pages to RGB PIL images (needs pypdfium2)."""
    import pypdfium2 as pdfium

    data = pdf.read_bytes() if isinstance(pdf, Path) else pdf
    doc = pdfium.PdfDocument(data)
    try:
        indices = range(len(doc)) if page_indices is None else page_indices
        scale = px_per_mm * 25.4 / 72.0
        images = []
        for i in indices:
            page = doc[i]
            images.append(page.render(scale=scale).to_pil().convert("RGB"))
            page.close()
        return images
    finally:
        doc.close()


def write_preview(pdf_path: Path, png_path: Path, page: int = 0, px_per_mm: float = 5.0) -> Path:
    img = rasterize_pdf(Path(pdf_path), px_per_mm, [page])[0]
    png_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(png_path)
    return png_path
