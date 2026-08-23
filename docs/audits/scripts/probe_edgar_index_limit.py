"""Sizes edgar_events.index_limit. The slice is taken newest-first BEFORE the lookback
filter, so the limit must exceed the most matched filings any ticker files in one
lookback window -- otherwise a rare event is silently crowded out by routine 144s."""
import json, time, urllib.request, gzip, datetime, pathlib, collections

UA = "shortlist-research turgechr@duck.com"
def get(url, tries=3):
    for i in range(tries):
        try:
            r = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(r, timeout=30) as f:
                raw = f.read()
                if f.headers.get("Content-Encoding") == "gzip": raw = gzip.decompress(raw)
                return json.loads(raw)
        except Exception:
            if i == tries - 1: raise
            time.sleep(1.5)

CURRENT = {"8-K", "SC 13D", "SC 13G", "144", "SCHEDULE 13D", "SCHEDULE 13G", "10-Q", "10-K"}
NEW = CURRENT | {"NT 10-K", "NT 10-Q", "424B5", "S-3ASR", "S-3", "UPLOAD", "CORRESP"}
TODAY, LOOKBACK = datetime.date(2026, 8, 23), 90

root = pathlib.Path("/home/chris/shortlist/src/shortlist/backtest")
tickers = sorted({ln.split()[0].upper() for f in ("universe_largecap.txt", "universe_smallmid.txt")
                  for ln in (root / f).read_text().splitlines() if ln.strip() and not ln.startswith("#")})
m = get("https://www.sec.gov/files/company_tickers.json")
t2c = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in m.values()}

def matched_in_window(forms, dates, allowed):
    n = 0
    for i, fm in enumerate(forms):
        if fm.strip().upper() not in allowed: continue
        try: age = (TODAY - datetime.date.fromisoformat(dates[i])).days
        except Exception: continue
        if 0 <= age <= LOOKBACK: n += 1
    return n

cur, new = collections.Counter(), collections.Counter()
for k, tk in enumerate(tickers):
    cik = t2c.get(tk)
    if not cik: continue
    try: sub = get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    except Exception: continue
    r = sub.get("filings", {}).get("recent", {})
    f_, d_ = r.get("form", []), r.get("filingDate", [])
    cur[tk] = matched_in_window(f_, d_, CURRENT)
    new[tk] = matched_in_window(f_, d_, NEW)
    time.sleep(0.12)
    if k % 60 == 0: print(f"  {k}/{len(tickers)}", flush=True)

def report(name, c):
    v = sorted(c.values(), reverse=True)
    over = {t: n for t, n in c.items() if n > 40}
    print(f"\n{name}: n={len(v)} max={v[0]} p99={v[len(v)//100]} p95={v[len(v)//20]} median={v[len(v)//2]}")
    print(f"  tickers exceeding the current limit of 40: {len(over)}  {dict(sorted(over.items(), key=lambda kv:-kv[1])[:8])}")
report("CURRENT 8-form filter", cur)
report("PROPOSED 15-form filter", new)
