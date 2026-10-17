import json, importlib.util, re
spec = importlib.util.spec_from_file_location("f", "news_filter_v5.py"); f = importlib.util.module_from_spec(spec); spec.loader.exec_module(f)
SETS = [("f1","labels_out",{"AAPL":"Apple Inc.","GEV":"GE Vernova Inc.","CROX":"Crocs, Inc.","SMCI":"Super Micro Computer, Inc.","PYPL":"PayPal Holdings, Inc.","CALM":"Cal-Maine Foods, Inc.","PLAB":"Photronics, Inc.","BMY":"Bristol-Myers Squibb Company","LMT":"Lockheed Martin Corporation","UBER":"Uber Technologies, Inc.","BSX":"Boston Scientific Corporation","CELH":"Celsius Holdings, Inc.","JPM":"JPMorgan Chase & Co.","HIMS":"Hims & Hers Health, Inc.","UFPT":"UFP Technologies, Inc."}),
        ("f2","labels3_out",{"BA":"The Boeing Company","NKE":"NIKE, Inc.","INTC":"Intel Corporation","PTON":"Peloton Interactive, Inc.","BYND":"Beyond Meat, Inc.","SNAP":"Snap Inc.","ON":"ON Semiconductor Corporation","MRNA":"Moderna, Inc.","CAT":"Caterpillar Inc."})]
for K in (8, 10, 12, 15):
    kept=hit=hi=hih=0
    for raw, lab, T in SETS:
        for t, n in T.items():
            ks, _ = f.select(json.load(open(f"{raw}/{t}.raw.json")), t, n, k=K)
            ev = json.load(open(f"{lab}/{t}.json"))["events"]
            eids = [{str(i) for i in e["ids"]} for e in ev]; kids = [{str(i) for i in c["ids"]} for c in ks]
            kept += len(ks); hit += sum(1 for k in kids if any(k & e for e in eids))
            for e, s in zip(ev, eids):
                if e["tier"] == "high": hi += 1; hih += any(k & s for k in kids)
    print(f"K={K}: precision {hit}/{kept} ({hit/kept:.0%}) high_recall {hih}/{hi}")
