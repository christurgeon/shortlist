"""Precision probe for EDGAR full-text search as an earnings-quality signal.

The question: does a phrase hit distinguish a company that HAS the problem from
boilerplate? A phrase that hits ~every 10-K carries no information."""
import json, time, urllib.request, urllib.parse, pathlib, collections

UA = "shortlist-research turgechr@duck.com"
def fts(phrase, cik, forms="10-K", start="2024-08-23", end="2026-08-23"):
    qs = urllib.parse.urlencode({"q": f'"{phrase}"', "forms": forms, "ciks": cik,
                                 "dateRange": "custom", "startdt": start, "enddt": end})
    req = urllib.request.Request(f"https://efts.sec.gov/LATEST/search-index?{qs}",
                                 headers={"User-Agent": UA})
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as f:
                d = json.load(f)
            return d["hits"]["total"]["value"], [h["_source"]["adsh"] for h in d["hits"]["hits"]]
        except Exception:
            if i == 2: return None, []
            time.sleep(2.0)

base = json.load(open("/tmp/claude-1000/-home-chris-shortlist/8518e3ec-608c-4017-9989-04760019e244/scratchpad/forms_baserate.json"))
m = json.load(urllib.request.urlopen(urllib.request.Request(
    "https://www.sec.gov/files/company_tickers.json", headers={"User-Agent": UA})))
t2c = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in m.values()}
tickers = sorted(set(base["per_form"]["365d"]["8-K"]))
pairs = [(t, t2c[t]) for t in tickers if t in t2c]
print(f"{len(pairs)} tickers")

PHRASES = {
  "A_material_weakness":       "material weakness",
  "B_icfr_not_effective":      "internal control over financial reporting was not effective",
  "C_were_not_effective":      "were not effective",
  "D_going_concern_doubt":     "substantial doubt about our ability to continue as a going concern",
  "E_did_not_maintain":        "did not maintain effective internal control over financial reporting",
}
res = collections.defaultdict(dict)
t0 = time.time()
for key, phrase in PHRASES.items():
    hits = []
    for i, (tk, cik) in enumerate(pairs):
        n, adsh = fts(phrase, cik)
        if n is None: continue
        res[key][tk] = n
        if n: hits.append(tk)
        time.sleep(0.10)
    c = len(hits)
    print(f"{key:<26} {c:>4}/{len(pairs)}  {100*c/len(pairs):>5.1f}%   ({time.time()-t0:.0f}s)  e.g. {hits[:10]}")

pathlib.Path("/tmp/claude-1000/-home-chris-shortlist/8518e3ec-608c-4017-9989-04760019e244/scratchpad/fts_precision.json").write_text(json.dumps(res))
b = {t for t, n in res["B_icfr_not_effective"].items() if n} | {t for t, n in res["E_did_not_maintain"].items() if n}
a = {t for t, n in res["A_material_weakness"].items() if n}
print(f"\nadverse-language names (B or E): {sorted(b)}")
print(f"'material weakness' names not in B/E (i.e. boilerplate-only): {len(a - b)}")
