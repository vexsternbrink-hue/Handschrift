import pytest

from handschrift import users
from handschrift.charset import fallback_chain, normalize_text
from handschrift.config import HandschriftError


def test_create_load_list_remove(workdir):
    p = users.create_user("freund1", "Freund Eins")
    assert p.input_dir.is_dir() and p.output_dir.is_dir() and p.glyph_dir.is_dir()
    assert users.load_user("freund1").display_name == "Freund Eins"
    assert "freund1" in [u.user_id for u in users.list_users()]
    with pytest.raises(HandschriftError):
        users.create_user("freund1")
    assert users.create_user("freund1", exist_ok=True).user_id == "freund1"
    p.ink = "schwarz"
    p.save()
    assert users.load_user("FREUND1").ink == "schwarz"
    users.delete_user("freund1")
    with pytest.raises(HandschriftError):
        users.load_user("freund1")


@pytest.mark.parametrize("bad", ["", "../etc", "a b", "x" * 40, "Ä"])
def test_invalid_user_names(bad):
    with pytest.raises(HandschriftError):
        users.validate_user_id(bad)


def test_normalize_text():
    assert normalize_text("a\r\nb–c’d…") == "a\nb-c'd..."


def test_fallback_chain():
    assert fallback_chain("é")[:2] == ["é", "e"]
    assert "ss" in fallback_chain("ß")
    assert fallback_chain("q")[-1] == "Q"
