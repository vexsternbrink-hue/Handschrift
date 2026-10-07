#!/usr/bin/env python3
"""Builds the self-contained web app: web/index.html

    python web/build.py

Inlines web/core.js, the demo handwriting (web/demo_profile.json) and the
printable template PDF into web/app.html. The result is a single file that
works offline-capable in any modern browser (iPad Safari included) and is
what gets published.

If web/demo_profile.json is missing it is created from users/demo
(run `python main.py overnight` first).
"""
import base64
import json
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent
ROOT = WEB.parent


def demo_profile() -> dict:
    path = WEB / "demo_profile.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    hw = ROOT / "users" / "demo" / "handwriting"
    data = json.loads((hw / "glyphs.json").read_text(encoding="utf-8"))
    glyphs = {}
    for ch, recs in data["glyphs"].items():
        glyphs[ch] = [
            {
                "w": r["width"], "h": r["height"], "b": round(r["baseline"], 2), "tb": round(r["template_baseline"], 2),
                "il": r["ink_left"], "ir": r["ink_right"], "it": r["ink_top"], "ib": r["ink_bottom"],
                "png": base64.b64encode((hw / "glyphs" / r["file"]).read_bytes()).decode("ascii"),
            }
            for r in recs
        ]
    profile = {
        "v": 1, "name": "Demo-Handschrift", "created": data["created"], "pxPerMm": data["px_per_mm"],
        "xHeightPx": data["x_height_px"], "capHeightPx": data["cap_height_px"], "missing": data["missing"], "glyphs": glyphs,
    }
    path.write_text(json.dumps(profile, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return profile


def main() -> int:
    html = (WEB / "app.html").read_text(encoding="utf-8")
    core = (WEB / "core.js").read_text(encoding="utf-8")
    demo = json.dumps(demo_profile(), ensure_ascii=False, separators=(",", ":"))
    pdf = base64.b64encode((ROOT / "templates" / "handschrift_vorlage.pdf").read_bytes()).decode("ascii")
    for part, name in ((core, "core.js"), (demo, "demo profile")):
        if "</script" in part.lower():
            raise SystemExit(f"{name} contains '</script'")
    for marker, value in (("/*__CORE__*/", core), ("/*__DEMO_PROFILE__*/", demo), ("/*__TEMPLATE_PDF__*/", pdf)):
        if html.count(marker) != 1:
            raise SystemExit(f"marker {marker} must appear exactly once in app.html")
        html = html.replace(marker, value)
    out = WEB / "index.html"
    out.write_text(html, encoding="utf-8")
    print(f"{out} ({out.stat().st_size / 1024:.0f} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
