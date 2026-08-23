"""OUT-OF-SAMPLE test: 120 held-out small/mid caps the rule has never seen.
Narrow phrases drive the detector; two broad nets run alongside purely to catch
positives the narrow set would miss (a recall check, not an input to the rule)."""
import json, re, time, html, urllib.request, urllib.parse, datetime, random, pathlib, sys
sys.path.insert(0, "/tmp/claude-1000/-home-chris-shortlist/8518e3ec-608c-4017-9989-04760019e244/scratchpad")
from rule import is_current_adverse          # the exact rule, unchanged
UA = "shortlist-research turgechr@duck.com"
SP = pathlib.Path("/tmp/claude-1000/-home-chris-shortlist/8518e3ec-608c-4017-9989-04760019e244/scratchpad")

def j(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=60))
def doc(u):
    raw = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=150).read(40_000_000)
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw.decode("utf8", "replace"))
    return re.sub(r"[\s ]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t)))

seen = set()
for f in ("universe_largecap.txt", "universe_smallmid.txt"):
    for ln in open(f"/home/chris/shortlist/src/shortlist/backtest/{f}"):
        ln = ln.strip()
        if ln and not ln.startswith("#"): seen.add(ln.split()[0].upper())
uni = json.load(open("/home/chris/shortlist/.cache/nasdaq_universe/2026-08-10.json"))
pool = [t for t, (mc, px) in uni.items() if mc and 3e8 <= mc <= 5e9 and t not in seen and t.isalpha()]
random.seed(20260823); sample = sorted(random.sample(pool, 120))
print(f"held-out sample: {len(sample)} names, $300M-$5B, disjoint from the 228\n{sample}\n", flush=True)

m = j("https://www.sec.gov/files/company_tickers.json")
t2c = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in m.values()}
NARROW = ["internal control over financial reporting was not effective",
          "internal controls over financial reporting were not effective",
          "did not maintain effective internal control over financial reporting",
          "disclosure controls and procedures were not effective"]
NETS = ["identified a material weakness", "material weaknesses in our internal control over financial reporting"]

def fts(phrase, cik):
    qs = urllib.parse.urlencode({"q": f'"{phrase}"', "forms": "10-K", "ciks": cik,
                                 "dateRange": "custom", "startdt": "2024-08-23", "enddt": "2026-08-23"})
    for i in range(4):
        try:
            d = j(f"https://efts.sec.gov/LATEST/search-index?{qs}")
            return [(h["_source"]["adsh"], h["_source"].get("period_ending"), h["_id"].split(":", 1)[1],
                     h["_source"]["form"], h["_source"]["file_date"]) for h in d["hits"]["hits"]]
        except Exception:
            if i == 3: return []
            time.sleep(2.5)

narrow_hits, net_hits, docs = {}, {}, {}
t0 = time.time()
for k, tk in enumerate(sample):
    cik = t2c.get(tk)
    if not cik: continue
    for p in NARROW:
        for h in fts(p, cik):
            narrow_hits.setdefault(tk, set()).add(h[0]); docs[(tk, h[0])] = h
        time.sleep(0.10)
    for p in NETS:
        for h in fts(p, cik):
            net_hits.setdefault(tk, set()).add(h[0]); docs[(tk, h[0])] = h
        time.sleep(0.10)
    if k % 20 == 0: print(f"  {k}/{len(sample)} ({time.time()-t0:.0f}s)", flush=True)

print(f"\nnarrow-phrase tickers: {len(narrow_hits)}  {sorted(narrow_hits)}")
print(f"broad-net tickers:     {len(net_hits)}  {sorted(net_hits)}")
print(f"net-only (recall risk): {sorted(set(net_hits) - set(narrow_hits))}\n", flush=True)

results = {}
for (tk, adsh), (_, pe, fn, form, fdate) in sorted(docs.items()):
    if not pe: continue
    url = f"https://www.sec.gov/Archives/edgar/data/{int(t2c[tk])}/{adsh.replace('-','')}/{fn}"
    try: txt = doc(url)
    except Exception as e:
        print(f"  ! {tk} {adsh}: {e}", flush=True); continue
    pred, detail = is_current_adverse(txt, datetime.date.fromisoformat(pe))
    results[f"{tk}:{adsh}"] = {"pred": pred, "form": form, "filed": fdate, "period": pe,
                              "detail": [(d[0][:40], d[1], d[2]) for d in detail],
                              "net_only": tk in (set(net_hits) - set(narrow_hits))}
    if pred:
        i = txt.lower().find([p for p in NARROW if p in txt.lower()][0])
        print(f"\n+++ POSITIVE {tk} {form} filed {fdate} period={pe} {adsh}\n    …{txt[max(0,i-300):i+220].strip()}…", flush=True)
    time.sleep(0.3)
(SP / "oos_results.json").write_text(json.dumps(results, indent=1))
pos = sorted({k.split(':')[0] for k, v in results.items() if v["pred"]})
print(f"\nOUT-OF-SAMPLE: {len(pos)}/{len(sample)} tickers flagged ({100*len(pos)/len(sample):.1f}%): {pos}")
