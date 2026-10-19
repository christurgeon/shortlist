"""Acceptance names: real events, duplicates and junk among shown items, from the hand tags."""
import importlib.util, json, sys
sys.path.insert(0, "docs/audits/scripts/dedupe_2026-10-18")
labels = json.load(open("docs/audits/scripts/dedupe_2026-10-18/accept_labels.json"))
names = {"XOM": "Exxon Mobil Corporation", "ABBV": "AbbVie Inc.", "PGR": "The Progressive Corporation", "NFLX": "Netflix, Inc.",
         "CMG": "Chipotle Mexican Grill, Inc.", "ISRG": "Intuitive Surgical, Inc.", "DUOL": "Duolingo, Inc.", "IPAR": "Interparfums, Inc."}
def load(path, mod):
    s = importlib.util.spec_from_file_location(mod, path); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
for label, path in [("v7", "docs/audits/scripts/news_filter_v7.py"), ("v8", "docs/audits/scripts/news_filter_v8.py")]:
    flt = load(path, label)
    ev = dup = junk = unk = shown = 0
    per = {}
    for t, n in names.items():
        arts = json.load(open(f"scratch/news_panel_2026-10-18/accept/raw/{t}.raw.json"))
        items = flt.select(arts, t, n, k=10)[0]
        seen, d = set(), 0
        for c in items:
            tags = {labels.get(t, {}).get(str(i)) for i in c["ids"]} - {None}
            real = {x for x in tags if not x.startswith("x:")}
            shown += 1
            if not tags: unk += 1
            elif not real: junk += 1
            elif real <= seen: dup += 1; d += 1
            else: ev += 1
            seen |= real
        per[t] = f"{len(items)}/{d}"
    print(f"{label}: shown {shown} events {ev} dups {dup} junk {junk} untagged {unk} | per ticker shown/dups {per}")
