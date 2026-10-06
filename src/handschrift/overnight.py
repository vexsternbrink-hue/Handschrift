"""The "overnight build": one command that sets everything up and tests it.

Steps (each one is logged; a failing step does not stop the following ones):
  1. check the environment
  2. generate the printable template
  3. create demo users and simulate photos of filled templates
  4. train on those photos and compare the result with the known truth
  5. render example PDFs (+ PNG previews) and export the font
  6. stress test: many random renderings must all produce valid PDFs
  7. run the automated test-suite (pytest)
  8. write a report (logs/overnight_report.md) and copy results to examples/
"""

from __future__ import annotations

import logging
import platform
import random
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import EXAMPLES_DIR, PROJECT_ROOT, TEMPLATE_FILENAME, TEMPLATES_DIR, logs_dir

log = logging.getLogger("handschrift.overnight")

DEMO_USERS = [
    # user id, display name, demo font, ink, paper
    ("demo", "Demo", "Caveat-Regular.ttf", "blau", "liniert"),
    ("demo2", "Demo Zwei", "PatrickHand-Regular.ttf", "schwarz", "kariert"),
]
TEXTS_DIR = EXAMPLES_DIR / "texte"


@dataclass
class Step:
    name: str
    ok: bool = False
    seconds: float = 0.0
    details: list[str] = field(default_factory=list)
    error: str = ""


class Build:
    def __init__(self) -> None:
        self.steps: list[Step] = []

    def run(self, name: str, fn, *args, **kwargs):
        step = Step(name)
        self.steps.append(step)
        log.info("=" * 70)
        log.info("SCHRITT: %s", name)
        t0 = time.time()
        result = None
        try:
            result = fn(step, *args, **kwargs)
            step.ok = True
        except Exception as exc:  # keep going – the report shows what failed
            step.error = f"{type(exc).__name__}: {exc}"
            log.error("Schritt fehlgeschlagen: %s\n%s", name, traceback.format_exc())
        step.seconds = time.time() - t0
        log.info("-> %s (%.1f s)", "OK" if step.ok else "FEHLER", step.seconds)
        return result

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.steps)


# --------------------------------------------------------------------------

def step_environment(step: Step) -> None:
    from importlib.metadata import PackageNotFoundError, version

    import cv2

    info = {"Python": platform.python_version(), "System": f"{platform.system()} {platform.release()}"}
    for pkg in ("numpy", "opencv-python-headless", "Pillow", "reportlab", "fonttools", "pypdfium2", "pytest"):
        try:
            info[pkg] = version(pkg)
        except PackageNotFoundError:
            info[pkg] = "(nicht als Paket gefunden)"
    info["OpenCV (geladen)"] = cv2.__version__
    if sys.version_info < (3, 9):
        raise RuntimeError("Python 3.9 oder neuer wird benötigt")
    if not hasattr(cv2, "aruco"):
        raise RuntimeError("OpenCV ohne ArUco-Modul – bitte opencv-python-headless >= 4.7 installieren")
    for k, v in info.items():
        step.details.append(f"{k}: {v}")
        log.info("  %-24s %s", k, v)


def step_template(step: Step) -> Path:
    from .template import write_preview, write_template

    pdf = write_template(TEMPLATES_DIR / TEMPLATE_FILENAME, 3)
    png = write_preview(pdf, TEMPLATES_DIR / "handschrift_vorlage_vorschau.png")
    step.details += [f"Template: {pdf.relative_to(PROJECT_ROOT)}", f"Vorschau: {png.relative_to(PROJECT_ROOT)}"]
    return pdf


def step_demo_user(step: Step, user_id: str, display: str, font: str, ink: str, paper: str):
    from . import users
    from .glyphs import GlyphSet
    from .simulate import bbox_iou, make_demo_inputs
    from .train import train_user

    profile = users.get_or_create_user(user_id)
    profile.display_name, profile.ink, profile.paper = display, ink, paper
    profile.save()
    for old in profile.input_dir.glob("*"):
        if old.is_file():
            old.unlink()
    truth = make_demo_inputs(profile.input_dir, font, pages=2, seed=sum(map(ord, user_id)), name=display)
    report = train_user(profile)
    step.details.append(report.summary().replace("\n", " | "))

    gs = GlyphSet.load(profile.handwriting_dir)
    ious, wrong = [], []
    for ch, recs in gs.glyphs.items():
        for r in recs:
            page = int(r.source.rsplit("#S", 1)[1]) - 1
            gt = truth.get(page, {}).get(ch)
            iou = bbox_iou(gt, r.page_bbox_mm) if gt else 0.0
            ious.append(iou)
            if iou < 0.3:
                wrong.append(f"{ch}@S{page + 1}")
    mean_iou = sum(ious) / max(1, len(ious))
    step.details.append(
        f"Abgleich mit Soll-Positionen: {len(ious)} Glyphen, mittlere Überdeckung {mean_iou:.2f}, falsch zugeordnet: {len(wrong)}"
    )
    log.info("  Abgleich: mittlere Überdeckung %.2f, falsch: %s", mean_iou, wrong or "keine")
    if report.chars_missing:
        raise RuntimeError(f"Zeichen nicht gefunden: {' '.join(report.chars_missing)}")
    if wrong or mean_iou < 0.7:
        raise RuntimeError(f"Segmentierung ungenau (Überdeckung {mean_iou:.2f}, falsch: {wrong[:10]})")
    return profile


def step_render_examples(step: Step, profiles) -> list[Path]:
    from .fontexport import export_font
    from .glyphs import GlyphSet
    from .render import RenderOptions, render_text

    jobs = [
        ("demo", "brief.txt", "beispiel_brief", RenderOptions(seed=11, preview=True)),
        ("demo", "erlkoenig.txt", "beispiel_erlkoenig", RenderOptions(seed=12, preview=True)),
        ("demo2", "einkaufsliste.txt", "beispiel_einkaufsliste_kariert", RenderOptions(paper="kariert", ink="schwarz", seed=13, preview=True)),
        ("demo2", "brief.txt", "beispiel_brief_demo2", RenderOptions(seed=14, preview=True)),
    ]
    produced = []
    for user_id, text_file, name, opts in jobs:
        profile = profiles.get(user_id)
        if profile is None:
            step.details.append(f"übersprungen ({user_id} nicht trainiert): {name}")
            continue
        gs = GlyphSet.load(profile.handwriting_dir)
        text = (TEXTS_DIR / text_file).read_text(encoding="utf-8")
        opts.title = f"{name} – Handschrift von {profile.display_name}"
        res = render_text(gs, text, profile.output_dir / f"{name}.pdf", opts)
        if res.missing:
            raise RuntimeError(f"{name}: fehlende Zeichen {res.missing}")
        produced += [res.pdf, *res.previews]
        step.details.append(f"{res.pdf.relative_to(PROJECT_ROOT)}: {res.pages} Seite(n), {res.glyphs} Zeichen")
    for user_id, profile in profiles.items():
        gs = GlyphSet.load(profile.handwriting_dir)
        ttf = export_font(gs, profile.handwriting_dir / f"handschrift_{user_id}.ttf", f"Handschrift {profile.display_name}")
        _check_font(ttf)
        produced.append(ttf)
        step.details.append(f"Schriftart: {ttf.relative_to(PROJECT_ROOT)}")
    return produced


def _check_font(ttf: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(str(ttf), 48)
    img = Image.new("L", (900, 80), 255)
    ImageDraw.Draw(img).text((5, 10), "Grüße aus Köln, 2,50 €!", font=font, fill=0)
    if img.getextrema()[0] > 100:
        raise RuntimeError(f"{ttf.name}: Schrift zeichnet nichts")


def _random_text(rng: random.Random, chars: list[str]) -> str:
    words = []
    for _ in range(rng.randint(1, 400)):
        r = rng.random()
        if r < 0.03:
            words.append("\n")
        elif r < 0.04:
            words.append("\n\n")
        elif r < 0.045:
            words.append("Donaudampfschifffahrtsgesellschaftskapitänsmützenabzeichen" * rng.randint(1, 3))
        elif r < 0.05:
            words.append(rng.choice(["😀", "é", "ñ", "Ω", "—", "…", "«Zitat»", "\t", "ẞ", "#"]))
        else:
            words.append("".join(rng.choice(chars) for _ in range(rng.randint(1, 12))))
    return " ".join(words)


def step_stress(step: Step, profiles, count: int) -> None:
    import pypdfium2 as pdfium

    from .glyphs import GlyphSet
    from .paper import PAPER_STYLES
    from .render import INK_COLORS, RenderOptions, render_text

    if not profiles:
        raise RuntimeError("keine trainierten Demo-Nutzer")
    rng = random.Random(4711)
    with tempfile.TemporaryDirectory(prefix="handschrift-stress-") as tmp:
        for i in range(count):
            user_id = rng.choice(sorted(profiles))
            gs = GlyphSet.load(profiles[user_id].handwriting_dir)  # fresh load = "reuse" like a new session
            opts = RenderOptions(
                paper=rng.choice(PAPER_STYLES), ink=rng.choice(list(INK_COLORS)),
                size=rng.uniform(0.6, 1.6), jitter=rng.uniform(0, 2), seed=i,
                line_spacing=rng.choice([None, 7.0, 8.5, 10.0, 12.0]),
            )
            text = _random_text(rng, gs.chars)
            if not text.strip():
                text = "leer"
            out = Path(tmp) / f"stress_{i}.pdf"
            res = render_text(gs, text, out, opts)
            doc = pdfium.PdfDocument(str(out))
            pages = len(doc)
            doc.close()
            if pages != res.pages or pages < 1:
                raise RuntimeError(f"Stress-Lauf {i}: PDF hat {pages} statt {res.pages} Seiten")
    step.details.append(f"{count} zufällige Renderings (verschiedene Nutzer, Papier, Farben, Größen) – alle gültig")


def step_pytest(step: Step) -> None:
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(PROJECT_ROOT / "tests")]
    log.info("  %s", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=1800)
    for line in (proc.stdout + proc.stderr).strip().splitlines():
        log.info("  | %s", line)
    last = (proc.stdout.strip().splitlines() or ["?"])[-1]
    step.details.append(f"pytest: {last}")
    if proc.returncode != 0:
        raise RuntimeError(f"Tests fehlgeschlagen ({last})")


def step_examples(step: Step, profiles, produced: list[Path]) -> None:
    """Copy the interesting results into examples/ so they're easy to find."""
    from PIL import Image

    EXAMPLES_DIR.mkdir(exist_ok=True)
    out_dir = EXAMPLES_DIR / "ausgabe"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir()
    for p in produced or []:
        if p.suffix in {".pdf", ".png"}:
            shutil.copy2(p, out_dir / p.name)
    demo = profiles.get("demo")
    if demo:
        scans = sorted(demo.root.glob("scan_seite*.png"))
        if scans:
            images = [Image.open(s).convert("RGB") for s in scans]
            target = EXAMPLES_DIR / "beispiel_vorlage_ausgefuellt.pdf"
            images[0].save(target, save_all=True, append_images=images[1:], resolution=8 * 25.4)
            step.details.append(f"{target.relative_to(PROJECT_ROOT)} (simuliert ausgefülltes Template)")
        photo = demo.input_dir / "foto_seite1.jpg"
        if photo.exists():
            shutil.copy2(photo, EXAMPLES_DIR / "beispiel_foto_handy.jpg")
        for f in sorted(demo.debug_dir.glob("*.jpg"))[:1]:
            shutil.copy2(f, EXAMPLES_DIR / "beispiel_kontrollbild_training.jpg")
        ov = demo.handwriting_dir / "glyph_uebersicht.png"
        if ov.exists():
            shutil.copy2(ov, EXAMPLES_DIR / "beispiel_glyph_uebersicht.png")
    step.details.append(f"Beispiele kopiert nach {out_dir.relative_to(PROJECT_ROOT)}/")


def write_report(build: Build, started: datetime, log_file: Path | None) -> Path:
    lines = [
        "# Overnight-Build – Bericht",
        "",
        f"* Start: {started:%Y-%m-%d %H:%M:%S}",
        f"* Ende: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"* Ergebnis: **{'ALLES OK' if build.ok else 'FEHLER – siehe unten'}**",
    ]
    if log_file:
        lines.append(f"* Ausführliches Log: `{log_file}`")
    lines += ["", "| Schritt | Status | Dauer |", "|---|---|---|"]
    for s in build.steps:
        lines.append(f"| {s.name} | {'✅ OK' if s.ok else '❌ FEHLER'} | {s.seconds:.1f} s |")
    for s in build.steps:
        lines += ["", f"## {s.name}"]
        lines += [f"- {d}" for d in s.details]
        if s.error:
            lines.append(f"- **Fehler:** `{s.error}`")
    path = logs_dir() / "overnight_report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_overnight(stress: int = 25, run_tests: bool = True, log_file: Path | None = None) -> int:
    started = datetime.now()
    log.info("Overnight-Build gestartet %s", started.isoformat(timespec="seconds"))
    build = Build()
    build.run("Umgebung prüfen", step_environment)
    build.run("Template erzeugen", step_template)
    profiles = {}
    for user_id, display, font, ink, paper in DEMO_USERS:
        p = build.run(f"Demo-Nutzer '{user_id}': Foto simulieren + trainieren", step_demo_user, user_id, display, font, ink, paper)
        if p is not None:
            profiles[user_id] = p
    produced = build.run("Beispiel-PDFs + Schriftart erzeugen", step_render_examples, profiles)
    build.run(f"Stabilitätstest ({stress} Renderings)", step_stress, profiles, stress)
    if run_tests:
        build.run("Automatische Tests (pytest)", step_pytest)
    build.run("Beispiele nach examples/ kopieren", step_examples, profiles, produced)

    report = write_report(build, started, log_file)
    try:
        shutil.copy2(report, EXAMPLES_DIR / "letzter_overnight_bericht.md")
    except OSError:
        log.warning("Bericht konnte nicht nach examples/ kopiert werden")
    log.info("=" * 70)
    log.info("ERGEBNIS: %s", "ALLES OK" if build.ok else "ES GAB FEHLER")
    for s in build.steps:
        log.info("  [%s] %s", "OK" if s.ok else "!!", s.name)
    log.info("Bericht: %s", report)
    return 0 if build.ok else 1
