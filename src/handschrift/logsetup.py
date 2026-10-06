"""Logging: readable console output plus a rotating log file in ``logs/``."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from .config import logs_dir

FILE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(verbose: bool = False, extra_file: Path | None = None) -> Path:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    root.setLevel(logging.DEBUG)

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s" if verbose else "%(message)s"))
    root.addHandler(console)

    directory = logs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    main_log = directory / "handschrift.log"
    fh = logging.handlers.RotatingFileHandler(main_log, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(FILE_FORMAT))
    root.addHandler(fh)

    if extra_file is not None:
        extra_file.parent.mkdir(parents=True, exist_ok=True)
        xh = logging.FileHandler(extra_file, encoding="utf-8")
        xh.setLevel(logging.DEBUG)
        xh.setFormatter(logging.Formatter(FILE_FORMAT))
        root.addHandler(xh)

    for noisy in ("PIL", "fontTools", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return main_log
