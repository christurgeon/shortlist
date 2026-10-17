"""Write blind-labelling inputs (id, date, source, headline, summary) from frozen Finnhub payloads.

Usage: probe_news_dump_for_labels.py RAWDIR OUTDIR [--all] TICKER=Name ...
Without --all, only articles whose headline matches the v4 aliases are written (tuning and
held-out 1). With --all, every article is written (held-out 2), so name-matching misses count
against the filter instead of being hidden from the labeller.
"""
import json, os, sys, importlib.util
from datetime import datetime, timezone

here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("v4", os.path.join(here, "news_filter_v4.py"))
v4 = importlib.util.module_from_spec(spec); spec.loader.exec_module(v4)

raw, out = sys.argv[1], sys.argv[2]
args = sys.argv[3:]
every = "--all" in args
os.makedirs(out, exist_ok=True)
for pair in (a for a in args if a != "--all"):
    t, name = pair.split("=", 1)
    arts = json.load(open(f"{raw}/{t}.raw.json"))
    pats = v4.aliases(t, name)
    seen, rows = set(), []
    for a in sorted(arts, key=lambda a: a.get("datetime") or 0, reverse=True):
        h = (a.get("headline") or "").strip()
        if not h or h in seen or not (every or any(p.search(h) for p in pats)):
            continue
        seen.add(h)
        d = datetime.fromtimestamp(a["datetime"], tz=timezone.utc).date().isoformat()
        rows.append(f"{a['id']}\t{d}\t{a.get('source')}\t{h}\t{(a.get('summary') or '')[:200]}".replace("\n", " "))
    open(f"{out}/{t}.tsv", "w").write("id\tdate\tsource\theadline\tsummary\n" + "\n".join(rows) + "\n")
    print(t, len(rows))
