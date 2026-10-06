import pypdfium2 as pdfium
import pytest

from handschrift.config import HandschriftError
from handschrift.paper import PaperSpec
from handschrift.render import Renderer, RenderOptions, parse_ink, render_text


def _pages(path):
    doc = pdfium.PdfDocument(str(path))
    try:
        return len(doc)
    finally:
        doc.close()


def test_render_simple_text(glyphset, tmp_path):
    res = render_text(glyphset, "Hallo Welt! Grüße aus Köln, 2,50 €.", tmp_path / "a.pdf", RenderOptions(seed=1, preview=True))
    assert res.pdf.is_file() and _pages(res.pdf) == 1
    assert res.missing == {}
    assert res.glyphs == len("HalloWelt!GrüßeausKöln,2,50€.")
    assert res.previews and res.previews[0].is_file()


def test_lines_stay_inside_the_margins(glyphset):
    r = Renderer(glyphset, RenderOptions(seed=2))
    available = r.paper.text_right - r.paper.text_left
    lines = r.layout("Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 30)
    assert len(lines) > 5
    for line in lines:
        start, word = line[-1]
        assert start + word.width <= available + 1e-6


def test_very_long_word_is_split(glyphset):
    r = Renderer(glyphset, RenderOptions(seed=3))
    lines = r.layout("Donaudampfschifffahrtsgesellschaftskapitän" * 4)
    assert len(lines) >= 2


def test_long_text_gives_several_pages(glyphset, tmp_path):
    text = "\n".join(f"Zeile {i}: Das ist ein Test." for i in range(80))
    res = render_text(glyphset, text, tmp_path / "lang.pdf", RenderOptions(seed=4))
    assert res.pages >= 3 and _pages(res.pdf) == res.pages


def test_unknown_characters_are_reported(glyphset, tmp_path):
    res = render_text(glyphset, "Café 😀", tmp_path / "x.pdf", RenderOptions(seed=5))
    assert "😀" in res.missing  # skipped
    assert "é" in res.missing  # replaced by e


def test_empty_text_is_rejected(glyphset, tmp_path):
    with pytest.raises(HandschriftError):
        render_text(glyphset, "   \n ", tmp_path / "leer.pdf")


@pytest.mark.parametrize("paper", ["liniert", "kariert", "blanko"])
def test_paper_styles(glyphset, tmp_path, paper):
    res = render_text(glyphset, "Papier-Test", tmp_path / f"{paper}.pdf", RenderOptions(paper=paper, seed=6))
    assert _pages(res.pdf) == 1


def test_paper_geometry():
    lined = PaperSpec("liniert", 8.5)
    ys = lined.writing_lines()
    assert ys[0] == pytest.approx(15 + 8.5)
    assert ys[-1] <= 297 - 15
    grid = PaperSpec("kariert", 9.0)
    assert grid.line_spacing == 10.0
    assert all(abs((y - 15) % 5) < 1e-6 for y in grid.writing_lines())


def test_same_seed_same_layout(glyphset):
    a = Renderer(glyphset, RenderOptions(seed=42)).layout("Der gleiche Text")
    b = Renderer(glyphset, RenderOptions(seed=42)).layout("Der gleiche Text")
    assert [(x, w.width) for x, w in a[0]] == [(x, w.width) for x, w in b[0]]


def test_ink_colors():
    assert parse_ink("blau") != parse_ink("schwarz")
    assert parse_ink("#ff0000") == (1.0, 0.0, 0.0)
    with pytest.raises(HandschriftError):
        parse_ink("lila-gepunktet")
