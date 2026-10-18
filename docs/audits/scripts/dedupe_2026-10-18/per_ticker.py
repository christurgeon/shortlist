"""Acceptance breakdown per ticker: shown / events / dups / junk. Usage: per_ticker.py opt_x ..."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evaluate as E  # noqa: E402
from hidden import resolve  # noqa: E402

labels = E.load(os.path.join(os.path.dirname(os.path.abspath(__file__)), "accept_labels.json"))
for name in sys.argv[1:]:
    mod = resolve(name)
    row = []
    for t, n in E.ACCEPT_NAMES.items():
        items, _ = mod.select(E.load(f"{E.ACCEPT}/{t}.raw.json"), t, n, E.K)
        seen, d = set(), 0
        for it in items:
            lab = labels[t].get(E.face_id(E.load(f"{E.ACCEPT}/{t}.raw.json"), it), "?")
            d += lab in seen
            seen.add(lab)
        row.append(f"{t} {len(items)}/{d}")
    print(f"{name:24}", "  ".join(row), "  (shown/dups)")
