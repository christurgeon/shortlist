"""Score a frozen filter module on all 24 labelled names at K=10, and check that production
`shortlist.research.news.select` returns the same items (date, report count, headline).

Usage: uv run python docs/audits/scripts/probe_news_eval_k10.py FILTER.py PROBE_DIR
PROBE_DIR holds the raw payloads (f1/, f2/) and labels (labels_out/, labels3_out/). The raw
payloads are local-only (scratch/news_probe_2026-10-16 in the main checkout).
"""
import html
import importlib.util
import json
import sys

from shortlist.research import news

SETS = [("f1", "labels_out", {"AAPL": "Apple Inc.", "GEV": "GE Vernova Inc.", "CROX": "Crocs, Inc.",
                              "SMCI": "Super Micro Computer, Inc.", "PYPL": "PayPal Holdings, Inc.",
                              "CALM": "Cal-Maine Foods, Inc.", "PLAB": "Photronics, Inc.",
                              "BMY": "Bristol-Myers Squibb Company", "LMT": "Lockheed Martin Corporation",
                              "UBER": "Uber Technologies, Inc.", "BSX": "Boston Scientific Corporation",
                              "CELH": "Celsius Holdings, Inc.", "JPM": "JPMorgan Chase & Co.",
                              "HIMS": "Hims & Hers Health, Inc.", "UFPT": "UFP Technologies, Inc."}),
        ("f2", "labels3_out", {"BA": "The Boeing Company", "NKE": "NIKE, Inc.", "INTC": "Intel Corporation",
                               "PTON": "Peloton Interactive, Inc.", "BYND": "Beyond Meat, Inc.",
                               "SNAP": "Snap Inc.", "ON": "ON Semiconductor Corporation",
                               "MRNA": "Moderna, Inc.", "CAT": "Caterpillar Inc."})]
K = 10

spec = importlib.util.spec_from_file_location("flt", sys.argv[1])
flt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flt)
root = sys.argv[2]


def norm(h):
    return news.clean(html.unescape(h), news._HEADLINE_MAX)


kept = hit = hi = hih = shown_ev = dups = 0
mismatch = []
for raw, lab, names in SETS:
    for t, n in names.items():
        arts = json.load(open(f"{root}/{raw}/{t}.raw.json"))
        ks, _ = flt.select(arts, t, n, k=K)
        prod = news.select(arts, t, n, K)
        a = sorted((c["date"], len(c["ids"]), norm(c["headline"])) for c in ks)
        b = sorted((i.date, i.reports, i.headline) for i in prod)
        if a != b:
            mismatch.append((t, sorted(set(a) - set(b)), sorted(set(b) - set(a))))
        ev = json.load(open(f"{root}/{lab}/{t}.json"))["events"]
        eids = [{str(i) for i in e["ids"]} for e in ev]
        kids = [{str(i) for i in c["ids"]} for c in ks]
        kept += len(ks)
        hit += sum(1 for k in kids if any(k & e for e in eids))
        # Precision counts a second item about an event as a hit; these two do not.
        seen = set()
        for k in kids:
            touched = {j for j, e in enumerate(eids) if k & e}
            dups += bool(touched) and touched <= seen
            seen |= touched
        shown_ev += len(seen)
        for e, s in zip(ev, eids):
            if e["tier"] == "high":
                hi += 1
                hih += any(k & s for k in kids)
print(f"{sys.argv[1].rsplit('/', 1)[-1]} K={K}: precision {hit}/{kept} ({hit / kept:.0%}) "
      f"high_recall {hih}/{hi} distinct_events {shown_ev} duplicate_items {dups}")
print("production == filter:", "yes" if not mismatch else "NO")
for t, only_f, only_p in mismatch:
    print(f"  {t}: only in filter {only_f}\n       only in production {only_p}")
