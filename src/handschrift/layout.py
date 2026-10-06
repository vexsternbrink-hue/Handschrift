"""Geometry of the fill-in template (all values in millimetres, origin top-left).

The same numbers are used to *draw* the template and to *read* it back from a
photo, so the two can never get out of sync.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .charset import CHARSET

PAGE_W = 210.0
PAGE_H = 297.0

# ArUco corner markers (4x4 bit dictionary). Each template page has its own
# four marker ids so a page can be recognised even if photos come in any order.
MARKER_SIZE = 14.0
MARKER_MARGIN = 10.0
MAX_PAGES = 12  # DICT_4X4_50 has 50 ids -> 12 pages with 4 markers each

# Character grid
COLS = 11
BOX_W = 16.0
BOX_H = 24.0
GAP_X = 1.0
LABEL_H = 4.5
ROW_GAP = 0.5
GRID_X0 = (PAGE_W - (COLS * BOX_W + (COLS - 1) * GAP_X)) / 2.0
GRID_Y0 = 31.0
ROWS = -(-len(CHARSET) // COLS)

# Guide lines inside every box, measured from the top edge of the box.
BASELINE_FROM_TOP = 15.0
MIDLINE_FROM_TOP = 9.0  # x-height guide: lower-case letters reach up to here

# When cutting a character out of a box, stay this far away from the border.
CROP_INSET = 1.1


@dataclass(frozen=True)
class Slot:
    index: int
    char: str
    x: float  # box left
    y: float  # box top
    w: float
    h: float

    @property
    def baseline_y(self) -> float:
        return self.y + BASELINE_FROM_TOP

    @property
    def midline_y(self) -> float:
        return self.y + MIDLINE_FROM_TOP

    @property
    def label_pos(self) -> tuple[float, float]:
        return (self.x + 0.6, self.y - 1.2)


def slots() -> list[Slot]:
    result = []
    for i, ch in enumerate(CHARSET):
        row, col = divmod(i, COLS)
        x = GRID_X0 + col * (BOX_W + GAP_X)
        y = GRID_Y0 + row * (LABEL_H + BOX_H + ROW_GAP) + LABEL_H
        result.append(Slot(i, ch, x, y, BOX_W, BOX_H))
    return result


# Order of the four markers: top-left, top-right, bottom-right, bottom-left.
def marker_origins() -> list[tuple[float, float]]:
    near, far_x, far_y = MARKER_MARGIN, PAGE_W - MARKER_MARGIN - MARKER_SIZE, PAGE_H - MARKER_MARGIN - MARKER_SIZE
    return [(near, near), (far_x, near), (far_x, far_y), (near, far_y)]


def marker_ids(page: int) -> list[int]:
    if not 0 <= page < MAX_PAGES:
        raise ValueError(f"page must be in 0..{MAX_PAGES - 1}")
    return [4 * page + k for k in range(4)]


def page_of_marker(marker_id: int) -> int:
    return marker_id // 4


def marker_corners_mm(marker_id: int) -> np.ndarray:
    """The 4 corners of a marker in page millimetres, in ArUco's corner order
    (top-left, top-right, bottom-right, bottom-left of the upright marker)."""
    x, y = marker_origins()[marker_id % 4]
    s = MARKER_SIZE
    return np.array([[x, y], [x + s, y], [x + s, y + s], [x, y + s]], dtype=np.float64)


def _check_layout() -> None:
    last = slots()[-1]
    bottom_marker_top = PAGE_H - MARKER_MARGIN - MARKER_SIZE
    assert last.y + last.h < bottom_marker_top - 2, "grid overlaps bottom markers"
    assert GRID_X0 > 0


_check_layout()
