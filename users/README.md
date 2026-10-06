# Nutzer-Ordner

Hier legt die App für jeden Nutzer einen eigenen Ordner an:

```
users/<name>/
    profile.json        Name + persönliche Standard-Einstellungen
    input/              Fotos/Scans des ausgefüllten Templates hier hineinlegen
    handwriting/        gelernte Handschrift (Zeichenbilder, glyphs.json, Kontrollbilder, .ttf)
    output/             erzeugte PDFs
```

Der Inhalt wird von Git ignoriert (persönliche Daten).
Die Demo-Nutzer `demo` und `demo2` werden von `python main.py overnight` erzeugt.
