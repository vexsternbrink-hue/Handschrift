# Overnight-Build – Bericht

* Start: 2026-10-06 20:05:55
* Ende: 2026-10-06 20:07:38
* Ergebnis: **ALLES OK**
* Ausführliches Log: `/home/user/Handschrift/logs/overnight_20261006_200555.log`

| Schritt | Status | Dauer |
|---|---|---|
| Umgebung prüfen | ✅ OK | 0.1 s |
| Template erzeugen | ✅ OK | 0.2 s |
| Demo-Nutzer 'demo': Foto simulieren + trainieren | ✅ OK | 17.5 s |
| Demo-Nutzer 'demo2': Foto simulieren + trainieren | ✅ OK | 14.5 s |
| Beispiel-PDFs + Schriftart erzeugen | ✅ OK | 2.2 s |
| Stabilitätstest (25 Renderings) | ✅ OK | 34.0 s |
| Automatische Tests (pytest) | ✅ OK | 34.1 s |
| Beispiele nach examples/ kopieren | ✅ OK | 0.2 s |

## Umgebung prüfen
- Python: 3.13.16
- System: Linux 6.18.44-fc-v70
- numpy: 2.5.3
- opencv-python-headless: 5.0.0.93
- Pillow: 12.3.0
- reportlab: 5.0.1
- fonttools: 4.66.1
- pypdfium2: 5.14.0
- pytest: 9.1.1
- OpenCV (geladen): 5.0.0

## Template erzeugen
- Template: templates/handschrift_vorlage.pdf
- Vorschau: templates/handschrift_vorlage_vorschau.png

## Demo-Nutzer 'demo': Foto simulieren + trainieren
- Nutzer: demo | Verarbeitete Seiten: 2  (fehlgeschlagen: 0) | Gelernte Zeichen: 88/88  (Varianten insgesamt: 176) | Kontrollbild: /home/user/Handschrift/users/demo/handwriting/glyph_uebersicht.png
- Abgleich mit Soll-Positionen: 176 Glyphen, mittlere Überdeckung 0.89, falsch zugeordnet: 0

## Demo-Nutzer 'demo2': Foto simulieren + trainieren
- Nutzer: demo2 | Verarbeitete Seiten: 2  (fehlgeschlagen: 0) | Gelernte Zeichen: 88/88  (Varianten insgesamt: 176) | Kontrollbild: /home/user/Handschrift/users/demo2/handwriting/glyph_uebersicht.png
- Abgleich mit Soll-Positionen: 176 Glyphen, mittlere Überdeckung 0.85, falsch zugeordnet: 0

## Beispiel-PDFs + Schriftart erzeugen
- users/demo/output/beispiel_brief.pdf: 1 Seite(n), 347 Zeichen
- users/demo/output/beispiel_erlkoenig.pdf: 2 Seite(n), 1113 Zeichen
- users/demo2/output/beispiel_einkaufsliste_kariert.pdf: 1 Seite(n), 163 Zeichen
- users/demo2/output/beispiel_brief_demo2.pdf: 1 Seite(n), 347 Zeichen
- Schriftart: users/demo/handwriting/handschrift_demo.ttf
- Schriftart: users/demo2/handwriting/handschrift_demo2.ttf

## Stabilitätstest (25 Renderings)
- 25 zufällige Renderings (verschiedene Nutzer, Papier, Farben, Größen) – alle gültig

## Automatische Tests (pytest)
- pytest: 33 passed in 33.62s

## Beispiele nach examples/ kopieren
- examples/beispiel_vorlage_ausgefuellt.pdf (simuliert ausgefülltes Template)
- Beispiele kopiert nach examples/ausgabe/
