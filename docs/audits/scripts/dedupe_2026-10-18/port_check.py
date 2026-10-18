"""Production news.select vs the recommended prototype, on every saved feed."""
import html
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import opt_final
from shortlist.research import news
names = {"AAPL": "Apple Inc.", "GEV": "GE Vernova Inc.", "CROX": "Crocs, Inc.", "SMCI": "Super Micro Computer, Inc.", "PYPL": "PayPal Holdings, Inc.", "CALM": "Cal-Maine Foods, Inc.", "PLAB": "Photronics, Inc.", "BMY": "Bristol-Myers Squibb Company", "LMT": "Lockheed Martin Corporation", "UBER": "Uber Technologies, Inc.", "BSX": "Boston Scientific Corporation", "CELH": "Celsius Holdings, Inc.", "JPM": "JPMorgan Chase & Co.", "HIMS": "Hims & Hers Health, Inc.", "UFPT": "UFP Technologies, Inc."}
names2 = {"BA": "The Boeing Company", "NKE": "NIKE, Inc.", "INTC": "Intel Corporation", "PTON": "Peloton Interactive, Inc.", "BYND": "Beyond Meat, Inc.", "SNAP": "Snap Inc.", "ON": "ON Semiconductor Corporation", "MRNA": "Moderna, Inc.", "CAT": "Caterpillar Inc."}
acc = {"XOM": "Exxon Mobil Corporation", "ABBV": "AbbVie Inc.", "PGR": "The Progressive Corporation", "NFLX": "Netflix, Inc.", "CMG": "Chipotle Mexican Grill, Inc.", "ISRG": "Intuitive Surgical, Inc.", "DUOL": "Duolingo, Inc.", "IPAR": "Interparfums, Inc."}
p1 = {"AXON": "Axon Enterprise, Inc.", "DECK": "Deckers Outdoor Corp", "ELF": "e.l.f. Beauty, Inc.", "JNJ": "Johnson & Johnson", "LMAT": "LeMaitre Vascular, Inc.", "MSFT": "Microsoft Corporation", "NVDA": "NVIDIA Corporation", "UNH": "UnitedHealth Group Incorporated"}
feeds = [(f"scratch/news_probe_2026-10-16/f1/{t}.raw.json", t, n) for t, n in names.items()]
feeds += [(f"scratch/news_probe_2026-10-16/f2/{t}.raw.json", t, n) for t, n in names2.items()]
feeds += [(f"scratch/news_panel_2026-10-18/accept/raw/{t}.raw.json", t, n) for t, n in acc.items()]
feeds += [(f"scratch/news_panel_2026-10-18/raw/{t}.raw.json", t, n) for t, n in p1.items()]
norm = lambda h: news.clean(html.unescape(h), news._HEADLINE_MAX)
same = 0
for path, t, n in feeds:
    arts = json.load(open(path))
    a = [(c["date"], c["outlets"], norm(c["headline"])) for c in opt_final.recommended.select(arts, t, n, 10)[0]]
    b = [(i.date, i.reports, i.headline) for i in news.select(arts, t, n, 10)]
    if sorted(a) == sorted(b):
        same += 1
    else:
        print(t, "DIFF\n  proto only:", sorted(set(a) - set(b)), "\n  prod only: ", sorted(set(b) - set(a)))
print(f"identical on {same}/{len(feeds)} feeds")
