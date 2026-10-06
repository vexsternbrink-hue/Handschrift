"""The learned handwriting of one user: glyph images + metrics (``glyphs.json``)."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from .charset import fallback_chain
from .config import HandschriftError

log = logging.getLogger(__name__)

FORMAT_VERSION = 1


@dataclass
class GlyphRecord:
    char: str
    variant: int
    file: str  # relative to the glyph directory
    width: int  # image size in px
    height: int
    baseline: float  # y of the baseline in image px (from the top)
    ink_left: int  # ink bounding box inside the image (px)
    ink_right: int
    ink_top: int
    ink_bottom: int
    template_baseline: float = 0.0  # where the printed guide line was (px)
    source: str = ""
    page_bbox_mm: list[float] = field(default_factory=list)  # ink bbox on the template page

    @property
    def ink_width(self) -> int:
        return self.ink_right - self.ink_left


@dataclass
class GlyphSet:
    directory: Path  # .../handwriting
    px_per_mm: float
    x_height_px: float
    cap_height_px: float
    glyphs: dict[str, list[GlyphRecord]]
    created: str = ""
    sources: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    _images: dict[str, Image.Image] = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------ io
    @property
    def glyph_dir(self) -> Path:
        return self.directory / "glyphs"

    @property
    def json_path(self) -> Path:
        return self.directory / "glyphs.json"

    def to_json(self) -> dict:
        return {
            "format_version": FORMAT_VERSION,
            "created": self.created,
            "px_per_mm": self.px_per_mm,
            "x_height_px": self.x_height_px,
            "cap_height_px": self.cap_height_px,
            "sources": self.sources,
            "missing": self.missing,
            "glyphs": {ch: [asdict(r) for r in recs] for ch, recs in self.glyphs.items()},
        }

    @classmethod
    def load(cls, handwriting_dir: Path) -> "GlyphSet":
        path = Path(handwriting_dir) / "glyphs.json"
        if not path.is_file():
            raise HandschriftError(
                f"Keine gelernte Handschrift gefunden ({path}). Bitte zuerst trainieren: "
                "python main.py train --user <name> --input <foto>"
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HandschriftError(f"{path} ist beschädigt: {exc}") from exc
        glyphs = {ch: [GlyphRecord(**r) for r in recs] for ch, recs in data["glyphs"].items()}
        gs = cls(
            directory=Path(handwriting_dir),
            px_per_mm=float(data["px_per_mm"]),
            x_height_px=float(data["x_height_px"]),
            cap_height_px=float(data["cap_height_px"]),
            glyphs={ch: recs for ch, recs in glyphs.items() if recs},
            created=data.get("created", ""),
            sources=data.get("sources", []),
            missing=data.get("missing", []),
        )
        absent = [r.file for recs in gs.glyphs.values() for r in recs if not (gs.glyph_dir / r.file).is_file()]
        if absent:
            raise HandschriftError(f"{len(absent)} Glyph-Dateien fehlen in {gs.glyph_dir} (z. B. {absent[0]}). Bitte neu trainieren.")
        return gs

    # ---------------------------------------------------------------- query
    @property
    def chars(self) -> list[str]:
        return list(self.glyphs)

    def variants(self, char: str) -> list[GlyphRecord]:
        return self.glyphs.get(char, [])

    def resolve(self, char: str) -> str | None:
        """Best available substitute for ``char`` (may be multi-character), or None."""
        for cand in fallback_chain(char):
            if all(c in self.glyphs for c in cand):
                return cand
        return None

    def alpha(self, rec: GlyphRecord) -> Image.Image:
        """Glyph coverage image (mode 'L', 255 = full ink)."""
        img = self._images.get(rec.file)
        if img is None:
            with Image.open(self.glyph_dir / rec.file) as f:
                img = f.convert("L")
                img.load()
            self._images[rec.file] = img
        return img


def overview_sheet(gs: GlyphSet, path: Path, cell_mm: float = 14.0) -> Path:
    """Draw every learned glyph (all variants) on a grid with its baseline, for checking."""
    from PIL import ImageDraw, ImageFont

    from .charset import CHARSET

    chars = [c for c in CHARSET if c in gs.glyphs] + [c for c in gs.glyphs if c not in CHARSET]
    max_var = max((len(v) for v in gs.glyphs.values()), default=1)
    cols = 12 if max_var == 1 else 6
    per_char_w = int(gs.px_per_mm * 3) + max_var * int(cell_mm * gs.px_per_mm * 0.85)
    cell_h = int(cell_mm * gs.px_per_mm * 1.6)
    rows = max(1, -(-len(chars) // cols))
    sheet = Image.new("RGB", (cols * per_char_w + 20, rows * cell_h + 20), "white")
    draw = ImageDraw.Draw(sheet)
    label_font = ImageFont.load_default(int(gs.px_per_mm * 2.6))
    for i, ch in enumerate(chars):
        r, c = divmod(i, cols)
        x0, y0 = 10 + c * per_char_w, 10 + r * cell_h
        base_y = y0 + int(cell_h * 0.68)
        draw.rectangle([x0, y0, x0 + per_char_w - 4, y0 + cell_h - 4], outline=(225, 225, 225))
        draw.line([x0 + 2, base_y, x0 + per_char_w - 6, base_y], fill=(170, 200, 240), width=2)
        draw.text((x0 + 4, y0 + 2), ch, fill=(200, 0, 0), font=label_font)
        x = x0 + int(gs.px_per_mm * 2.6)
        for rec in gs.glyphs[ch]:
            a = gs.alpha(rec)
            ink = Image.new("RGB", a.size, (20, 30, 90))
            top = int(base_y - rec.baseline)
            if x + a.width > x0 + per_char_w - 4:
                break
            sheet.paste(ink, (x, top), a)
            x += a.width + 6
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)
    return path


def glyph_alpha_array(gs: GlyphSet, rec: GlyphRecord) -> np.ndarray:
    return np.asarray(gs.alpha(rec), dtype=np.float32) / 255.0
