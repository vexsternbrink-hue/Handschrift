import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture(scope="session")
def workdir(tmp_path_factory):
    """All user data and logs of the test run go to a temporary folder."""
    base = tmp_path_factory.mktemp("handschrift")
    old = {k: os.environ.get(k) for k in ("HANDSCHRIFT_USERS_DIR", "HANDSCHRIFT_LOGS_DIR")}
    os.environ["HANDSCHRIFT_USERS_DIR"] = str(base / "users")
    os.environ["HANDSCHRIFT_LOGS_DIR"] = str(base / "logs")
    yield base
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(scope="session")
def trained(workdir):
    """A user trained on one simulated photo; returns (profile, ground truth)."""
    from handschrift import users
    from handschrift.simulate import make_demo_inputs
    from handschrift.train import train_user

    profile = users.create_user("testnutzer", "Test Nutzer")
    truth = make_demo_inputs(profile.input_dir, "Caveat-Regular.ttf", pages=1, seed=3)
    report = train_user(profile)
    return profile, truth, report


@pytest.fixture(scope="session")
def glyphset(trained):
    from handschrift.glyphs import GlyphSet

    return GlyphSet.load(trained[0].handwriting_dir)
