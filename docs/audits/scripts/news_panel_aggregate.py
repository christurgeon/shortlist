"""Aggregate the §8 news-usefulness panel: majority marks, agreement, and the kill rule.

Usage: uv run python docs/audits/scripts/news_panel_aggregate.py RATINGS_DIR [ITEM_KEY.json]
RATINGS_DIR holds ratings-r1.json .. ratings-r3.json. ITEM_KEY (local-only, third-party
headlines) is optional and only adds the headline to each item line.
Protocol: docs/audits/raw-2026-10-18-news-panel/protocol.md
"""
import json
import sys
from itertools import combinations
from pathlib import Path

D = Path(sys.argv[1])
R = [json.loads((D / f"ratings-r{r}.json").read_text()) for r in (1, 2, 3)]
key = json.loads(Path(sys.argv[2]).read_text()) if len(sys.argv) > 2 else {}
items = sorted(R[0]["items"], key=lambda k: (k.rsplit("-", 1)[0], int(k.rsplit("-", 1)[1])))
assert all(sorted(r["items"]) == sorted(items) for r in R), "raters marked different item sets"
want = {k: sum(r["items"][k]["mark"] == "WANT" for r in R) for k in items}
wanted = [k for k in items if want[k] >= 2]
tickers = sorted({k.rsplit("-", 1)[0] for k in items})
useful = [t for t in tickers if [r["sections"][t]["mark"] for r in R].count("USEFUL") >= 2]

n, N = 3, len(items)
pairwise = [sum(a["items"][k]["mark"] == b["items"][k]["mark"] for k in items) / N
            for a, b in combinations(R, 2)]
p_want = sum(want.values()) / (n * N)
p_e = p_want ** 2 + (1 - p_want) ** 2
p_bar = sum((w * (w - 1) + (n - w) * (n - w - 1)) / (n * (n - 1)) for w in want.values()) / N
kappa = (p_bar - p_e) / (1 - p_e)

item_fail = len(wanted) * 2 < N
section_fail = len(useful) < 0.4 * len(tickers)
print(f"items WANT {len(wanted)}/{N}; sections USEFUL {len(useful)}/{len(tickers)} {useful}")
print(f"pairwise agreement {[round(p, 2) for p in pairwise]}, Fleiss kappa {kappa:.2f}"
      + ("  (LOW CONFIDENCE)" if sum(pairwise) / 3 < 0.70 else ""))
print("kill rule:", "FIRES" if (item_fail or section_fail) else "passes",
      f"(items {'fail' if item_fail else 'pass'}, sections {'fail' if section_fail else 'pass'})")
for t in tickers:
    marks = [r["sections"][t]["mark"] for r in R]
    print(f"\n{t}: section {marks}")
    for k in (k for k in items if k.rsplit("-", 1)[0] == t):
        print(f"  {'Y' if k in wanted else '.'} {want[k]}/3 {k} {key.get(k, '')[:80]}")
