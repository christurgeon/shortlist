"""Base-rate probe: how often do the proposed EDGAR event forms actually appear,
and do they cost extra requests? Reads data.sec.gov/submissions/CIK*.json — the
SAME endpoint EdgarSource already hits once per ticker via edgartools."""
import json, time, urllib.request, collections, datetime, pathlib, sys

UA = "shortlist-research turgechr@duck.com"
def get(url, tries=3):
    for i in range(tries):
        try:
            r = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
            with urllib.request.urlopen(r, timeout=30) as f:
                raw = f.read()
                if f.headers.get("Content-Encoding") == "gzip":
                    import gzip; raw = gzip.decompress(raw)
                return json.loads(raw)
        except Exception as e:
            if i == tries - 1: raise
            time.sleep(1.5)

root = pathlib.Path("/home/chris/shortlist/src/shortlist/backtest")
tickers = []
for f in ("universe_largecap.txt", "universe_smallmid.txt"):
    for ln in (root / f).read_text().splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            tickers.append(ln.split()[0].upper())
tickers = sorted(set(tickers))
print(f"universe: {len(tickers)} tickers")

m = get("https://www.sec.gov/files/company_tickers.json")
t2c = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in m.values()}
resolved = [(t, t2c[t]) for t in tickers if t in t2c]
print(f"resolved to CIK: {len(resolved)}  unresolved: {len(tickers)-len(resolved)}")

TODAY = datetime.date(2026, 8, 23)
WINDOWS = {"90d": 90, "365d": 365}
INTEREST = ["NT 10-K", "NT 10-Q", "424B5", "S-3", "S-3ASR", "UPLOAD", "CORRESP", "25", "25-NSE", "15-12B", "15-12G"]

rows, item_counts, form_counts = [], collections.Counter(), collections.Counter()
per_form_tickers = {w: collections.defaultdict(set) for w in WINDOWS}
eightk_item_tickers = {w: collections.defaultdict(set) for w in WINDOWS}
n_filings_total = 0
t0 = time.time()
for i, (tk, cik) in enumerate(resolved):
    try:
        sub = get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    except Exception as e:
        print(f"  ! {tk}: {e}"); continue
    r = sub.get("filings", {}).get("recent", {})
    forms, dates, items = r.get("form", []), r.get("filingDate", []), r.get("items", [])
    n_filings_total += len(forms)
    for j, fm in enumerate(forms):
        d = dates[j]
        try: age = (TODAY - datetime.date.fromisoformat(d)).days
        except Exception: continue
        it = items[j] if j < len(items) else ""
        for w, days in WINDOWS.items():
            if 0 <= age <= days:
                per_form_tickers[w][fm.strip().upper()].add(tk)
                if fm.strip().upper().startswith("8-K") and it:
                    for code in [c.strip() for c in it.split(",") if c.strip()]:
                        eightk_item_tickers[w][code].add(tk)
    time.sleep(0.12)
    if i % 40 == 0: print(f"  {i}/{len(resolved)} ({time.time()-t0:.0f}s)")

out = {"n_tickers": len(resolved), "n_filings_in_index": n_filings_total,
       "per_form": {w: {k: sorted(v) for k, v in d.items()} for w, d in per_form_tickers.items()},
       "eightk_items": {w: {k: sorted(v) for k, v in d.items()} for w, d in eightk_item_tickers.items()}}
p = pathlib.Path("/tmp/claude-1000/-home-chris-shortlist/8518e3ec-608c-4017-9989-04760019e244/scratchpad/forms_baserate.json")
p.write_text(json.dumps(out))
print(f"\nwrote {p}  ({time.time()-t0:.0f}s total, {len(resolved)+1} requests)")

n = len(resolved)
for w in WINDOWS:
    print(f"\n=== window {w} — tickers with >=1 filing of form (n={n}) ===")
    d = per_form_tickers[w]
    for fm in INTEREST:
        c = len(d.get(fm, ()))
        print(f"  {fm:<10} {c:>4}  {100*c/n:>5.1f}%")
    print(f"  -- top forms overall --")
    for fm, v in sorted(d.items(), key=lambda kv: -len(kv[1]))[:12]:
        print(f"     {fm:<14} {len(v):>4} {100*len(v)/n:>5.1f}%")
    print(f"  -- 8-K items --")
    for code, v in sorted(eightk_item_tickers[w].items(), key=lambda kv: -len(kv[1]))[:14]:
        print(f"     item {code:<7} {len(v):>4} {100*len(v)/n:>5.1f}%")
