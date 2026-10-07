"""Writes simulated template photos as raw greyscale files for the JS tests.

    python web/tests/make_fixtures.py <out_dir>

Each photo becomes <name>.gray (uint8, row-major) plus an entry in
fixtures.json with its size and the ground-truth ink boxes (mm).
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from handschrift.simulate import simulate_filled_page, simulate_photo  # noqa: E402

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
cases = [
    ("caveat_s1", 0, "Caveat-Regular.ttf", 11, 0),
    ("caveat_s2_rot90", 1, "Caveat-Regular.ttf", 12, 90),
    ("patrick_s3_rot180", 2, "PatrickHand-Regular.ttf", 13, 180),
]
meta = []
for name, page, font, seed, rot in cases:
    clean, truth = simulate_filled_page(page, font, seed)
    photo = simulate_photo(clean, seed + 100)
    if rot:
        photo = photo.rotate(rot, expand=True)
    gray = np.asarray(photo.convert("L"), np.uint8)
    (out / f"{name}.gray").write_bytes(gray.tobytes())
    meta.append({"name": name, "page": page, "w": gray.shape[1], "h": gray.shape[0], "truth": truth})
(out / "fixtures.json").write_text(json.dumps(meta), encoding="utf-8")
print("ok", [m["name"] for m in meta])
