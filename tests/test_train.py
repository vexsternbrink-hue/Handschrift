import numpy as np
import pytest
from PIL import Image

from handschrift.charset import CHARSET
from handschrift.config import HandschriftError
from handschrift.simulate import bbox_iou, simulate_filled_page, simulate_photo
from handschrift.train import detect_page, process_page_image, train_user


def test_all_characters_learned(trained):
    _, _, report = trained
    assert report.chars_missing == []
    assert len(report.chars_found) == len(CHARSET)
    assert not report.failed_inputs


def test_glyphs_come_from_the_right_boxes(trained, glyphset):
    _, truth, _ = trained
    ious = []
    for ch, recs in glyphset.glyphs.items():
        iou = bbox_iou(truth[0][ch], recs[0].page_bbox_mm)
        assert iou > 0.3, f"{ch!r} looks like it was taken from the wrong box"
        ious.append(iou)
    assert np.mean(ious) > 0.75


def test_metrics_are_plausible(glyphset):
    xh = glyphset.x_height_px / glyphset.px_per_mm
    cap = glyphset.cap_height_px / glyphset.px_per_mm
    assert 2.5 < xh < 8 and xh < cap < 14
    # descenders hang below the baseline, normal letters sit on it
    g = glyphset.variants("g")[0]
    a = glyphset.variants("a")[0]
    assert g.height - g.baseline > 0.25 * glyphset.x_height_px
    assert abs(a.ink_bottom - a.baseline) < 1.0


def test_rotated_photo_is_recognised(workdir):
    clean, truth = simulate_filled_page(2, "PatrickHand-Regular.ttf", seed=5)
    photo = simulate_photo(clean, seed=9).rotate(180, expand=True)
    bgr = np.asarray(photo)[:, :, ::-1].copy()
    result = process_page_image(bgr, "kopfueber")
    assert result.page == 2
    assert len(result.glyphs) >= len(CHARSET) - 1
    by_char = {g.char: g for g in result.glyphs}
    assert bbox_iou(truth["A"], by_char["A"].page_bbox_mm) > 0.5


def test_image_without_markers_gives_clear_error():
    blank = np.full((1200, 900, 3), 255, np.uint8)
    with pytest.raises(HandschriftError, match="Marker"):
        detect_page(blank)


def test_bad_inputs_are_reported_not_fatal(workdir, tmp_path):
    from handschrift import users

    p = users.create_user("mitfehler")
    Image.new("RGB", (800, 1100), "white").save(tmp_path / "leer.jpg")
    (tmp_path / "kaputt.jpg").write_bytes(b"kein bild")
    with pytest.raises(HandschriftError):
        train_user(p, [tmp_path / "leer.jpg", tmp_path / "kaputt.jpg"])


def test_append_adds_variants(workdir):
    from handschrift import users
    from handschrift.glyphs import GlyphSet
    from handschrift.simulate import make_demo_inputs

    p = users.create_user("anhaengen")
    make_demo_inputs(p.input_dir, "Caveat-Regular.ttf", pages=1, seed=21)
    train_user(p)
    before = sum(len(v) for v in GlyphSet.load(p.handwriting_dir).glyphs.values())
    train_user(p, [p.input_dir / "foto_seite1.jpg"], append=True)
    after = GlyphSet.load(p.handwriting_dir)
    assert sum(len(v) for v in after.glyphs.values()) == 2 * before
    assert len({r.file for r in after.variants("a")}) == 2
