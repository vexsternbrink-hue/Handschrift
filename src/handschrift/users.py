"""User profiles: one folder per user below ``users/``.

    users/<id>/
        profile.json          name and personal defaults (ink colour, paper, size)
        input/                photos/scans of the filled template
        handwriting/          learned handwriting (glyph images + glyphs.json)
            glyphs/
            debug/            control images produced during training
        output/               generated PDFs
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path

from .config import HandschriftError, users_dir

log = logging.getLogger(__name__)

USER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def write_json_atomic(path: Path, data) -> None:
    """Write JSON so that a crash never leaves a half-written file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@dataclass
class UserProfile:
    user_id: str
    display_name: str = ""
    created: str = field(default_factory=now_iso)
    trained_at: str | None = None
    ink: str = "blau"
    paper: str = "liniert"
    size: float = 1.0

    # --- paths -----------------------------------------------------------
    @property
    def root(self) -> Path:
        return users_dir() / self.user_id

    @property
    def profile_path(self) -> Path:
        return self.root / "profile.json"

    @property
    def input_dir(self) -> Path:
        return self.root / "input"

    @property
    def handwriting_dir(self) -> Path:
        return self.root / "handwriting"

    @property
    def glyph_dir(self) -> Path:
        return self.handwriting_dir / "glyphs"

    @property
    def debug_dir(self) -> Path:
        return self.handwriting_dir / "debug"

    @property
    def output_dir(self) -> Path:
        return self.root / "output"

    @property
    def is_trained(self) -> bool:
        return (self.handwriting_dir / "glyphs.json").is_file()

    # --- persistence -----------------------------------------------------
    def ensure_dirs(self) -> None:
        for d in (self.input_dir, self.glyph_dir, self.debug_dir, self.output_dir):
            d.mkdir(parents=True, exist_ok=True)

    def save(self) -> None:
        self.ensure_dirs()
        write_json_atomic(self.profile_path, asdict(self))


def validate_user_id(user_id: str) -> str:
    uid = (user_id or "").strip().lower()
    if not USER_ID_RE.match(uid):
        raise HandschriftError(
            f"Ungültiger Nutzername '{user_id}'. Erlaubt: Kleinbuchstaben a-z, Ziffern, '-' und '_' "
            "(max. 32 Zeichen, z. B. 'vincent' oder 'freund1')."
        )
    return uid


def create_user(user_id: str, display_name: str | None = None, exist_ok: bool = False) -> UserProfile:
    uid = validate_user_id(user_id)
    if (users_dir() / uid / "profile.json").exists():
        if exist_ok:
            return load_user(uid)
        raise HandschriftError(f"Nutzer '{uid}' existiert bereits.")
    profile = UserProfile(user_id=uid, display_name=display_name or uid.capitalize())
    profile.save()
    log.info("Nutzer angelegt: %s (%s)", uid, profile.root)
    return profile


def load_user(user_id: str) -> UserProfile:
    uid = validate_user_id(user_id)
    path = users_dir() / uid / "profile.json"
    if not path.is_file():
        raise HandschriftError(
            f"Nutzer '{uid}' gibt es nicht. Lege ihn an mit:  python main.py user add {uid}"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HandschriftError(f"Profil von '{uid}' ist beschädigt ({path}): {exc}") from exc
    known = {f.name for f in fields(UserProfile)}
    profile = UserProfile(**{k: v for k, v in data.items() if k in known})
    profile.user_id = uid
    profile.ensure_dirs()
    return profile


def get_or_create_user(user_id: str) -> UserProfile:
    return create_user(user_id, exist_ok=True)


def list_users() -> list[UserProfile]:
    root = users_dir()
    if not root.is_dir():
        return []
    profiles = []
    for d in sorted(root.iterdir()):
        if d.is_dir() and (d / "profile.json").is_file():
            try:
                profiles.append(load_user(d.name))
            except HandschriftError as exc:
                log.warning("%s", exc)
    return profiles


def delete_user(user_id: str) -> Path:
    profile = load_user(user_id)
    shutil.rmtree(profile.root)
    log.info("Nutzer gelöscht: %s", profile.user_id)
    return profile.root
