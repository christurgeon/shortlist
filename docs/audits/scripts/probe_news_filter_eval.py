"""Score a filter module against blind labels. Usage: eval_filter.py MODULE RAWDIR LABELDIR TICKER=Name..."""
import importlib.util, json, sys, os
spec = importlib.util.spec_from_file_location("flt", sys.argv[1]); flt = importlib.util.module_from_spec(spec); spec.loader.exec_module(flt)
raw, lab = sys.argv[2], sys.argv[3]
tot = {"kept": 0, "kept_event": 0, "high": 0, "high_hit": 0, "ev": 0, "ev_hit": 0}
for pair in sys.argv[4:]:
    t, name = pair.split("=", 1)
    arts = json.load(open(f"{raw}/{t}.raw.json"))
    kept, _ = flt.select(arts, t, name)
    events = json.load(open(f"{lab}/{t}.json"))["events"]
    ev_ids = [{str(i) for i in e["ids"]} for e in events]
    kept_ids = [{str(i) for i in c["ids"]} for c in kept]
    k_ev = sum(1 for k in kept_ids if any(k & e for e in ev_ids))
    hit = [any(k & e for k in kept_ids) for e in ev_ids]
    hi = [h for h, e in zip(hit, events) if e["tier"] == "high"]
    missed_hi = [e["label"] for h, e in zip(hit, events) if e["tier"] == "high" and not h]
    print(f"{t:5} kept={len(kept):2} precision={k_ev}/{len(kept)}  high_recall={sum(hi)}/{len(hi)}  all_recall={sum(hit)}/{len(hit)}  missed_high={missed_hi}")
    tot["kept"] += len(kept); tot["kept_event"] += k_ev; tot["high"] += len(hi); tot["high_hit"] += sum(hi); tot["ev"] += len(hit); tot["ev_hit"] += sum(hit)
print(f"TOTAL precision={tot['kept_event']}/{tot['kept']} ({tot['kept_event']/max(tot['kept'],1):.0%})  high_recall={tot['high_hit']}/{tot['high']}  all_recall={tot['ev_hit']}/{tot['ev']} ({tot['ev_hit']/max(tot['ev'],1):.0%})")
