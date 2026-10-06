"""Central paths and constants.

All directories can be redirected with environment variables, which the tests
use to work in temporary folders:

* ``HANDSCHRIFT_USERS_DIR``  – where user profiles live (default: ``<project>/users``)
* ``HANDSCHRIFT_LOGS_DIR``   – where log files are written (default: ``<project>/logs``)
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = PROJECT_ROOT / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
EXAMPLES_DIR = PROJECT_ROOT / "examples"

# Resolution (pixels per millimetre) at which template photos are rectified and
# glyphs are stored. 12 px/mm ~ 305 dpi.
GLYPH_PX_PER_MM = 12.0

DEFAULT_TEMPLATE_PAGES = 3
TEMPLATE_FILENAME = "handschrift_vorlage.pdf"

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".heic", ".heif"}


def users_dir() -> Path:
    return Path(os.environ.get("HANDSCHRIFT_USERS_DIR", PROJECT_ROOT / "users"))


def logs_dir() -> Path:
    return Path(os.environ.get("HANDSCHRIFT_LOGS_DIR", PROJECT_ROOT / "logs"))


class HandschriftError(Exception):
    """An error with a message that is meant to be shown to the user as-is."""
