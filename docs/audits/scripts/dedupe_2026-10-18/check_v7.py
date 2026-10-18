"""Sanity: opt_seed == frozen v7 on all 24 + 8 names."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evaluate as E, opt_seed
from base import v7
bad = 0
allsets = [(t, n, a) for t, n, a, _ in E.labelled()] + [(t, n, E.load(f"{E.ACCEPT}/{t}.raw.json")) for t, n in E.ACCEPT_NAMES.items()]
for t, n, arts in allsets:
    a = sorted((c["date"], tuple(sorted(c["ids"]))) for c in opt_seed.select(arts, t, n, 10)[0])
    b = sorted((c["date"], tuple(sorted(str(i) for i in c["ids"]))) for c in v7.select(arts, t, n, k=10)[0])
    bad += a != b
print("opt_seed == v7:", "yes" if not bad else f"NO ({bad})")
