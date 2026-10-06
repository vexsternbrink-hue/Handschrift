#!/usr/bin/env python3
"""Handschrift-Generator – zentraler Einstiegspunkt.

    python main.py template                      # Ausfüll-Template erzeugen
    python main.py user add vincent              # Nutzer anlegen
    python main.py train --user vincent --input foto.jpg
    python main.py render --user vincent --input text.txt --output brief.pdf
    python main.py overnight                     # kompletter Erstlauf mit Demo + Tests

Alle Befehle: python main.py --help
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from handschrift.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
