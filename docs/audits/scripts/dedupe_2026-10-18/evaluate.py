"""Score dedupe prototypes with one harness.

Usage (from the repo root): uv run python <dedupe>/evaluate.py opt_seed opt_x ... [--show]

1. Dedupe vs ground truth, 24 labelled names: pairwise precision/recall/F1 of "same cluster"
   over labelled articles that survive v7's article filter (articles carrying two labels are
   skipped), and over-merge = sum over clusters of (distinct labelled events - 1); "hi" counts
   over-merges that involve a high-tier event.
2. Guard, as probe_news_eval_k10.py: K=10 precision (shown clusters touching any labelled
   event) and high-tier recall (must stay >= 43/47).
3. Acceptance, 8 names: shown items judged with accept_labels.json (face article id -> event
   label; "x:" labels are non-events). dups = shown items whose label already appears among
   the shown items; events = distinct real events shown; junk = distinct non-events shown.
"""
import importlib
import json
import os
import sys
import time
from itertools import combinations

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
REPO = "/Users/christopherturgeon/Documents/Code/shortlist"
PROBE = f"{REPO}/scratch/news_probe_2026-10-16"
ACCEPT = f"{REPO}/scratch/news_panel_2026-10-18/accept/raw"
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
ACCEPT_NAMES = {"XOM": "Exxon Mobil Corporation", "ABBV": "AbbVie Inc.", "PGR": "The Progressive Corporation",
                "NFLX": "Netflix, Inc.", "CMG": "Chipotle Mexican Grill, Inc.", "ISRG": "Intuitive Surgical, Inc.",
                "DUOL": "Duolingo, Inc.", "IPAR": "Interparfums, Inc."}
K = 10


def load(path):
    return json.load(open(path))


def labelled():
    for raw, lab, names in SETS:
        for t, n in names.items():
            yield t, n, load(f"{PROBE}/{raw}/{t}.raw.json"), load(f"{PROBE}/{lab}/{t}.json")["events"]


def score(mod, show=False):
    tp = fp = fn = om = om_hi = kept = hit = hi = hih = 0
    faced = faced_hi = shown_ev = shown_dup = shown_ev_hi = 0
    secs = 0.0
    briefs = 0
    for t, n, arts, ev in labelled():
        ev_of, tier = {}, {}
        for j, e in enumerate(ev):
            tier[j] = e["tier"]
            for i in e["ids"]:
                ev_of.setdefault(str(i), set()).add(j)
        single = {i: next(iter(s)) for i, s in ev_of.items() if len(s) == 1}
        t0 = time.perf_counter()
        cls = mod.clusters(arts, t, n)
        items, _ = mod.select(arts, t, n, K)
        secs += time.perf_counter() - t0
        briefs += 1
        cid = {}
        for ci, ms in enumerate(cls):
            evs = set()
            for m in ms:
                cid[m["id"]] = ci
                evs |= ev_of.get(m["id"], set())
            if len(evs) > 1:
                om += len(evs) - 1
                if any(tier[j] == "high" for j in evs):
                    om_hi += 1
                    if show:
                        print(f"  over-merge {t}: {[ev[j]['label'][:50] for j in evs]}")
        lab_ids = [i for i in single if i in cid]
        for a, b in combinations(lab_ids, 2):
            same_t, same_p = single[a] == single[b], cid[a] == cid[b]
            tp += same_t and same_p
            fp += same_p and not same_t
            fn += same_t and not same_p
        import base
        fev = set()
        for ms, f in base.rank(cls):
            if f["id"] in single:
                fev.add(single[f["id"]])
        faced += len(fev)
        faced_hi += sum(tier[j] == "high" for j in fev)
        seen = set()
        for it in items:
            j = single.get(face_id(arts, it))
            if j is None:
                continue
            if j in seen:
                shown_dup += 1
            else:
                seen.add(j)
                shown_ev += 1
                shown_ev_hi += tier[j] == "high"
        eids = [{str(i) for i in e["ids"]} for e in ev]
        kids = [set(c["ids"]) for c in items]
        kept += len(items)
        hit += sum(1 for k in kids if any(k & e for e in eids))
        for e, s in zip(ev, eids):
            if e["tier"] == "high":
                hi += 1
                hih += any(k & s for k in kids)
    p = tp / (tp + fp) if tp + fp else 0
    r = tp / (tp + fn) if tp + fn else 0
    return {"pairP": p, "pairR": r, "F1": 2 * p * r / (p + r) if p + r else 0, "overmerge": om,
            "om_hi": om_hi, "prec": f"{hit}/{kept}", "prec_pct": hit / kept, "hi": f"{hih}/{hi}",
            "ms_brief": 1000 * secs / briefs, "faced": faced, "faced_hi": faced_hi,
            "shown_ev": shown_ev, "shown_ev_hi": shown_ev_hi, "shown_dup": shown_dup}


def accept(mod, labels, show=False):
    dups = events = shown = junk = 0
    unknown = []
    for t, n in ACCEPT_NAMES.items():
        arts = load(f"{ACCEPT}/{t}.raw.json")
        items, _ = mod.select(arts, t, n, K)
        lab = labels.get(t, {})
        seen = set()
        for it in items:
            fid = face_id(arts, it)
            ev = lab.get(fid)
            shown += 1
            if ev is None:
                unknown.append((t, fid, it["headline"]))
                tag = "??"
            elif ev in seen:
                dups += 1
                tag = "D "
            elif ev.startswith("x:"):
                seen.add(ev)
                junk += 1
                tag = "x "
            else:
                seen.add(ev)
                events += 1
                tag = "E "
            if show:
                print(f"  {t:5} {tag} {it['date']} n={it['outlets']:<3} {fid} {it['headline'][:95]}")
    return {"shown": shown, "events": events, "dups": dups, "junk": junk, "unknown": unknown}


def face_id(arts, item):
    for a in arts:
        if str(a.get("id")) in item["ids"]:
            import html
            if html.unescape(a.get("headline") or "").strip() == item["headline"]:
                return str(a.get("id"))
    return item["ids"][0]


if __name__ == "__main__":
    show = "--show" in sys.argv
    labels_path = os.path.join(HERE, "accept_labels.json")
    labels = load(labels_path) if os.path.exists(labels_path) else {}
    for name in [a for a in sys.argv[1:] if not a.startswith("--")]:
        modname, _, var = name.partition(".")
        mod = importlib.import_module(modname)
        mod = getattr(mod, var) if var else mod
        s = score(mod, show)
        a = accept(mod, labels, show)
        print(f"{name:12} pairP {s['pairP']:.2f} pairR {s['pairR']:.2f} F1 {s['F1']:.2f} "
              f"overmerge {s['overmerge']:3} (hi {s['om_hi']}) | K10 prec {s['prec']} "
              f"({s['prec_pct']:.0%}) hi {s['hi']} | faced {s['faced']}/{s['faced_hi']}hi "
              f"shownEv {s['shown_ev']}/{s['shown_ev_hi']}hi dupItems {s['shown_dup']} | accept shown {a['shown']} events {a['events']} "
              f"dups {a['dups']} junk {a['junk']} unknown {len(a['unknown'])} | {s['ms_brief']:.1f} ms/brief")
        for u in a["unknown"]:
            print("   UNLABELLED", *u)
