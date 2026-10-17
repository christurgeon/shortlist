"""T3: share of labelled events that a /deep-selected 8-K could already cover.
An event counts as COVERED if the company filed an 8-K carrying an item /deep selects
(research.eightk.items) within +-5 days of the event's first report. Generous on purpose:
it ignores whether the 8-K's text actually describes the event (an upper bound on coverage)."""
import json, sys
from datetime import date
DEEP_ITEMS = {"4.02", "2.02", "2.01", "1.01", "5.02"}
# Which /deep-selected 8-K item could carry each label category. Anything else (analyst
# actions, litigation, third-party actions, trial results, policy) has no /deep 8-K home.
CAT_ITEMS = [("result", {"2.02"}), ("guidance", {"2.02"}), ("capital", {"2.02", "1.01"}),
             ("executive", {"5.02"}), ("board", {"5.02"}), ("m&a", {"1.01", "2.01"}),
             ("divest", {"1.01", "2.01"}), ("contract", {"1.01"}), ("partnership", {"1.01"})]


def _homes(cat):
    c = cat.lower()
    out = set()
    for k, items in CAT_ITEMS:
        if k in c:
            out |= items
    return out
idx = json.load(open(sys.argv[1])); lab = sys.argv[2]
tot = {"high": [0, 0], "medium": [0, 0]}
for t in sys.argv[3:]:
    ev = json.load(open(f"{lab}/{t}.json"))["events"]
    ks = [(date.fromisoformat(r["filed"]), set(r["items"].replace(" ", "").split(","))) for r in idx.get(t, [])]
    for e in ev:
        d = date.fromisoformat(e["first_date"])
        homes = _homes(e["category"])
        cov = [f"{fd} {sorted(it & homes)}" for fd, it in ks if abs((fd - d).days) <= 5 and it & homes]
        tot[e["tier"]][0] += 1; tot[e["tier"]][1] += bool(cov)
        if e["tier"] == "high" or cov:
            print(f"{t:5} {e['tier']:6} {e['first_date']} {'COVERED? ' + ';'.join(cov) if cov else 'novel   '} | {e['label'][:80]}")
for tier, (n, c) in tot.items():
    print(f"{tier}: {n} events, {c} possibly covered by a /deep 8-K, {n-c} novel ({(n-c)/max(n,1):.0%})")
