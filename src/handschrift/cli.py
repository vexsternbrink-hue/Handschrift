"""Command line interface: ``python main.py <befehl> ...``"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .config import DEFAULT_TEMPLATE_PAGES, TEMPLATE_FILENAME, TEMPLATES_DIR, HandschriftError
from .logsetup import setup_logging

log = logging.getLogger("handschrift")


def read_text_file(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    p = Path(path)
    if not p.is_file():
        raise HandschriftError(f"Textdatei nicht gefunden: {p}")
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def resolve_output(profile, output: str | None, stem: str) -> Path:
    if not output:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return profile.output_dir / f"{stem}_{stamp}.pdf"
    p = Path(output)
    if p.suffix.lower() != ".pdf":
        p = p.with_name(p.name + ".pdf")
    if not p.is_absolute() and p.parent == Path("."):
        return profile.output_dir / p.name  # plain file name -> the user's output folder
    return p


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_template(args) -> int:
    from .template import write_preview, write_template

    out = Path(args.output) if args.output else TEMPLATES_DIR / TEMPLATE_FILENAME
    write_template(out, args.pages)
    preview = write_preview(out, out.with_suffix(".png"))
    print(f"Template: {out}\nVorschau: {preview}")
    return 0


def cmd_user(args) -> int:
    from . import users

    if args.user_cmd == "add":
        p = users.create_user(args.name, args.display_name)
        print(f"Nutzer '{p.user_id}' angelegt: {p.root}")
        print(f"Lege jetzt das Foto deines ausgefüllten Templates in {p.input_dir} und starte:")
        print(f"  python main.py train --user {p.user_id}")
    elif args.user_cmd == "list":
        profiles = users.list_users()
        if not profiles:
            print("Noch keine Nutzer. Anlegen mit: python main.py user add <name>")
        for p in profiles:
            state = f"trainiert {p.trained_at}" if p.is_trained else "noch nicht trainiert"
            print(f"  {p.user_id:<16} {p.display_name:<20} {state}")
    elif args.user_cmd == "show":
        p = users.load_user(args.name)
        print(f"Nutzer:        {p.user_id} ({p.display_name})")
        print(f"Angelegt:      {p.created}")
        print(f"Trainiert:     {p.trained_at or 'nein'}")
        print(f"Tinte/Papier:  {p.ink} / {p.paper}, Größe {p.size}")
        print(f"Ordner:        {p.root}")
        if p.is_trained:
            from .glyphs import GlyphSet

            gs = GlyphSet.load(p.handwriting_dir)
            n = sum(len(v) for v in gs.glyphs.values())
            print(f"Zeichen:       {len(gs.glyphs)} ({n} Varianten)")
            if gs.missing:
                print(f"Fehlend:       {' '.join(gs.missing)}")
    elif args.user_cmd == "set":
        from .paper import PAPER_STYLES
        from .render import parse_ink

        p = users.load_user(args.name)
        if args.display_name:
            p.display_name = args.display_name
        if args.ink:
            parse_ink(args.ink)
            p.ink = args.ink
        if args.paper:
            if args.paper not in PAPER_STYLES:
                raise HandschriftError(f"Papier muss eines von {', '.join(PAPER_STYLES)} sein")
            p.paper = args.paper
        if args.size:
            p.size = args.size
        p.save()
        print(f"Gespeichert: {p.profile_path}")
    elif args.user_cmd == "remove":
        if not args.yes:
            raise HandschriftError("Löschen entfernt alle Daten des Nutzers. Zur Bestätigung --yes anhängen.")
        root = users.delete_user(args.name)
        print(f"Gelöscht: {root}")
    return 0


def cmd_train(args) -> int:
    from . import users
    from .train import train_user

    profile = users.get_or_create_user(args.user)
    inputs = [Path(i) for i in args.input] if args.input else None
    report = train_user(profile, inputs, append=args.append)
    print()
    print(report.summary())
    print(f"\nKontrollbilder (grün = Zeichen erkannt, rot = leer): {profile.debug_dir}")
    print(f"Jetzt Text schreiben:  python main.py render --user {profile.user_id} --text \"Hallo Welt\"")
    return 0 if not report.failed_inputs else 2


def cmd_render(args) -> int:
    from . import users
    from .glyphs import GlyphSet
    from .render import RenderOptions, render_text

    profile = users.load_user(args.user)
    gs = GlyphSet.load(profile.handwriting_dir)
    if args.text is not None:
        text, stem = args.text.replace("\\n", "\n"), "text"
    elif args.input:
        text = read_text_file(args.input)
        stem = "text" if args.input == "-" else Path(args.input).stem
    else:
        raise HandschriftError("Bitte --input <datei.txt> oder --text \"...\" angeben.")
    opts = RenderOptions(
        paper=args.paper or profile.paper,
        line_spacing=args.line_spacing,
        ink=args.ink or profile.ink,
        size=args.size or profile.size,
        jitter=args.jitter,
        seed=args.seed,
        margin_lines=not args.no_margin,
        preview=args.preview,
        title=f"Handschrift von {profile.display_name}",
    )
    out = resolve_output(profile, args.output, stem)
    result = render_text(gs, text, out, opts)
    print(f"PDF: {result.pdf}  ({result.pages} Seite(n))")
    for p in result.previews:
        print(f"Vorschau: {p}")
    return 0


def cmd_export_font(args) -> int:
    from . import users
    from .fontexport import export_font
    from .glyphs import GlyphSet

    profile = users.load_user(args.user)
    gs = GlyphSet.load(profile.handwriting_dir)
    out = Path(args.output) if args.output else profile.handwriting_dir / f"handschrift_{profile.user_id}.ttf"
    export_font(gs, out, f"Handschrift {profile.display_name}")
    print(f"Schriftart: {out}")
    return 0


def cmd_overnight(args) -> int:
    from .overnight import run_overnight

    return run_overnight(stress=args.stress, run_tests=not args.skip_tests, log_file=args._log_file)


# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python main.py",
        description="Handschrift-Generator: Handschrift aus einem Foto lernen und Texte als PDF schreiben.",
    )
    ap.add_argument("--version", action="version", version=f"handschrift {__version__}")
    ap.add_argument("-v", "--verbose", action="store_true", help="ausführliche Ausgabe")
    sub = ap.add_subparsers(dest="command", required=True, metavar="<befehl>")

    p = sub.add_parser("template", help="Ausfüll-Template (PDF) zum Ausdrucken erzeugen")
    p.add_argument("--pages", type=int, default=DEFAULT_TEMPLATE_PAGES, help="Anzahl Seiten (Standard 3; nur Seite 1 ist Pflicht)")
    p.add_argument("--output", help=f"Zieldatei (Standard templates/{TEMPLATE_FILENAME})")
    p.set_defaults(func=cmd_template)

    p = sub.add_parser("user", help="Nutzer verwalten (add, list, show, set, remove)")
    us = p.add_subparsers(dest="user_cmd", required=True, metavar="<aktion>")
    a = us.add_parser("add", help="neuen Nutzer anlegen")
    a.add_argument("name", help="Kurzname, z. B. vincent")
    a.add_argument("--display-name", help="Anzeigename")
    us.add_parser("list", help="alle Nutzer anzeigen")
    a = us.add_parser("show", help="Details zu einem Nutzer")
    a.add_argument("name")
    a = us.add_parser("set", help="Standard-Einstellungen eines Nutzers ändern")
    a.add_argument("name")
    a.add_argument("--display-name")
    a.add_argument("--ink", help="blau, dunkelblau, koenigsblau, schwarz, rot, gruen oder #RRGGBB")
    a.add_argument("--paper", help="liniert, kariert oder blanko")
    a.add_argument("--size", type=float, help="Schriftgröße-Faktor (1.0 = normal)")
    a = us.add_parser("remove", help="Nutzer mit allen Daten löschen")
    a.add_argument("name")
    a.add_argument("--yes", action="store_true", help="Löschen bestätigen")
    p.set_defaults(func=cmd_user)

    p = sub.add_parser("train", help="Handschrift aus Foto(s)/Scan(s) des ausgefüllten Templates lernen")
    p.add_argument("--user", required=True)
    p.add_argument("--input", nargs="+", help="Bilder/PDFs/Ordner (Standard: users/<name>/input/)")
    p.add_argument("--append", action="store_true", help="zusätzliche Varianten zur bestehenden Handschrift hinzufügen")
    p.set_defaults(func=cmd_train)

    p = sub.add_parser("render", help="Text in Handschrift als PDF schreiben")
    p.add_argument("--user", required=True)
    src = p.add_mutually_exclusive_group()
    src.add_argument("--input", help="Textdatei (UTF-8); '-' liest von der Standardeingabe")
    src.add_argument("--text", help="Text direkt angeben (\\n = neue Zeile)")
    p.add_argument("--output", help="PDF-Datei; nur ein Dateiname landet in users/<name>/output/")
    p.add_argument("--paper", choices=["liniert", "kariert", "blanko"])
    p.add_argument("--line-spacing", type=float, help="Zeilenabstand in mm (Standard 8.5, kariert 10)")
    p.add_argument("--ink", help="Tintenfarbe (blau, schwarz, ... oder #RRGGBB)")
    p.add_argument("--size", type=float, help="Schriftgröße-Faktor (1.0 = normal)")
    p.add_argument("--jitter", type=float, default=1.0, help="Natürlichkeit: 0 = exakt, 1 = normal, 2 = unordentlich")
    p.add_argument("--seed", type=int, help="Zufallswert für reproduzierbare Ergebnisse")
    p.add_argument("--no-margin", action="store_true", help="keine roten Randlinien")
    p.add_argument("--preview", action="store_true", help="zusätzlich PNG-Vorschau erzeugen")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("export-font", help="Handschrift zusätzlich als TrueType-Schrift (.ttf) exportieren")
    p.add_argument("--user", required=True)
    p.add_argument("--output")
    p.set_defaults(func=cmd_export_font)

    p = sub.add_parser("overnight", help="Kompletter Erstlauf: Template, Demo-Nutzer, Training, Beispiel-PDFs, Tests")
    p.add_argument("--stress", type=int, default=25, help="Anzahl zusätzlicher Zufalls-Renderings als Stabilitätstest")
    p.add_argument("--skip-tests", action="store_true", help="pytest nicht ausführen")
    p.set_defaults(func=cmd_overnight)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    extra = None
    if args.command == "overnight":
        from .config import logs_dir

        extra = logs_dir() / f"overnight_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    args._log_file = extra
    setup_logging(args.verbose, extra)
    log.debug("Befehl: %s", " ".join(sys.argv))
    try:
        return args.func(args)
    except HandschriftError as exc:
        log.error("Fehler: %s", exc)
        return 1
    except KeyboardInterrupt:
        log.error("Abgebrochen.")
        return 130
    except Exception:
        log.exception("Unerwarteter Fehler – Details stehen in logs/handschrift.log")
        return 1
