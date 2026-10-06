import cv2
import numpy as np

from handschrift import layout
from handschrift.charset import CHARSET
from handschrift.template import aruco_dictionary, build_template_pdf, rasterize_pdf


def test_every_character_has_a_box_inside_the_page():
    slots = layout.slots()
    assert [s.char for s in slots] == CHARSET
    assert len(set(CHARSET)) == len(CHARSET)
    for s in slots:
        assert 0 < s.x and s.x + s.w < layout.PAGE_W
        assert layout.MARKER_MARGIN + layout.MARKER_SIZE < s.y < s.y + s.h < layout.PAGE_H - layout.MARKER_MARGIN - layout.MARKER_SIZE
        assert s.y < s.midline_y < s.baseline_y < s.y + s.h


def test_boxes_do_not_overlap():
    slots = layout.slots()
    for a in slots:
        for b in slots:
            if a.index < b.index:
                overlap_x = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
                overlap_y = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
                assert overlap_x <= 0 or overlap_y <= 0


def test_template_pages_have_their_own_detectable_markers():
    pdf = build_template_pdf(3)
    images = rasterize_pdf(pdf, 6.0)
    assert len(images) == 3
    detector = cv2.aruco.ArucoDetector(aruco_dictionary(), cv2.aruco.DetectorParameters())
    for page, img in enumerate(images):
        _, ids, _ = detector.detectMarkers(cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY))
        assert ids is not None
        assert sorted(ids.ravel().tolist()) == layout.marker_ids(page)
