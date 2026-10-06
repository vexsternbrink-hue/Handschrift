"""Loading photos / scans of filled templates (JPG, PNG, HEIC, PDF, ...)."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .config import GLYPH_PX_PER_MM, IMAGE_SUFFIXES, HandschriftError

log = logging.getLogger(__name__)

MAX_SIDE = 6000  # larger photos are downscaled; more pixels don't help


def _register_heif() -> bool:
    try:
        import pillow_heif  # type: ignore

        pillow_heif.register_heif_opener()
        return True
    except ImportError:
        return False


def _pil_to_bgr(img: Image.Image) -> np.ndarray:
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        background = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        background.paste(rgba, mask=rgba.split()[3])
        img = background
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    arr = np.asarray(img)
    return np.ascontiguousarray(arr[:, :, ::-1])


def expand_inputs(paths: list[Path]) -> list[Path]:
    """Expand directories into the image/PDF files they contain."""
    files: list[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files += sorted(
                f for f in p.iterdir() if f.is_file() and (f.suffix.lower() in IMAGE_SUFFIXES or f.suffix.lower() == ".pdf")
            )
        elif p.is_file():
            files.append(p)
        else:
            raise HandschriftError(f"Eingabedatei nicht gefunden: {p}")
    return files


def load_pages(path: Path) -> list[tuple[str, np.ndarray]]:
    """Return ``(label, BGR image)`` for every page in ``path``."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from .template import rasterize_pdf

        images = rasterize_pdf(path, GLYPH_PX_PER_MM * 1.25)
        return [(f"{path.stem}_s{i + 1}", _pil_to_bgr(img)) for i, img in enumerate(images)]
    if suffix in {".heic", ".heif"} and not _register_heif():
        raise HandschriftError(
            f"{path.name}: HEIC-Bilder benötigen das Paket 'pillow-heif' (pip install pillow-heif). "
            "Alternativ am iPhone unter Einstellungen > Kamera > Formate 'Maximale Kompatibilität' wählen."
        )
    try:
        with Image.open(path) as img:
            img.load()
            return [(path.stem, _pil_to_bgr(img))]
    except (OSError, ValueError) as exc:
        raise HandschriftError(f"{path.name}: Bild kann nicht gelesen werden ({exc})") from exc
