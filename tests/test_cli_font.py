from PIL import Image, ImageDraw, ImageFont

from handschrift.cli import main
from handschrift.fontexport import export_font


def test_font_export(glyphset, tmp_path):
    ttf = export_font(glyphset, tmp_path / "test.ttf", "Handschrift Test")
    font = ImageFont.truetype(str(ttf), 40)
    img = Image.new("L", (600, 70), 255)
    ImageDraw.Draw(img).text((5, 5), "Hallo äöü ß", font=font, fill=0)
    assert img.getextrema()[0] < 100


def test_cli_end_to_end(trained, tmp_path, capsys):
    profile = trained[0]
    text = tmp_path / "text.txt"
    text.write_text("Ein Text aus einer Datei.\nZweite Zeile mit Umlauten: äöü.", encoding="utf-8")
    assert main(["render", "--user", profile.user_id, "--input", str(text), "--output", "cli.pdf"]) == 0
    assert (profile.output_dir / "cli.pdf").is_file()
    assert main(["render", "--user", profile.user_id, "--text", "Kurz\\nund knapp", "--paper", "kariert"]) == 0
    assert main(["user", "list"]) == 0
    assert profile.user_id in capsys.readouterr().out
    assert main(["user", "set", profile.user_id, "--ink", "schwarz"]) == 0
    assert main(["export-font", "--user", profile.user_id]) == 0


def test_cli_errors_are_friendly(workdir, capsys):
    assert main(["render", "--user", "gibtsnicht", "--text", "x"]) == 1
    assert main(["user", "add", "Ungültig Name"]) == 1
    assert main(["user", "remove", "testnutzer"]) == 1  # without --yes nothing is deleted
