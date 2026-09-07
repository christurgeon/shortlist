"""10-Q Part I Item 4 adverse-conclusion base rate over the two committed universes.

Stage 1: EDGAR full-text search finds candidate 10-Qs cheaply (keyless). Stage 2
downloads only the hits and runs the SHIPPED `research.controls.detect` against
them, twice: once with `_SELF_REF` as it ships, once widened to accept "this
quarterly report". The delta between the two is the measurement that decides
whether the regex gap is load-bearing on this path.

Throwaway probe. Full excerpts land in tenq_controls_hits.json for hand-labelling;
stdout stays a compact table so nothing large reaches the transcript.
"""
import collections
import datetime
import html
import json
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[3]   # repo root from docs/audits/scripts/
SP = pathlib.Path(__file__).parent
sys.path.insert(0, str(REPO / "src"))
from shortlist.research import controls  # the shipped detector, unmodified

UA = "shortlist-research turgechr@duck.com"
START, END = "2024-09-07", "2026-09-07"
CFG = controls.config_block(None)                # shipped defaults: window 240, tol 20

NARROW = list(controls._PHRASES)                 # exactly what the detector scans for
NETS = ["identified a material weakness",
        "material weaknesses in our internal control over financial reporting"]

# The widened self-reference. Shipped regex accepts "this (annual )?report" only;
# 10-Q Item 4 conventionally writes "this Quarterly Report".
WIDE_SELF_REF = re.compile(
    r"end of the period covered by this (annual |quarterly )?report", re.I)


def _get(url, timeout=60, raw=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as f:
        return f.read(40_000_000) if raw else json.load(f)


def fts(phrase, cik, forms="10-Q"):
    qs = urllib.parse.urlencode({"q": f'"{phrase}"', "forms": forms, "ciks": cik,
                                 "dateRange": "custom", "startdt": START, "enddt": END})
    for i in range(4):
        try:
            d = _get(f"https://efts.sec.gov/LATEST/search-index?{qs}", timeout=45)
            return [{"adsh": h["_source"]["adsh"],
                     "period": h["_source"].get("period_ending"),
                     "doc": h["_id"].split(":", 1)[1],
                     "form": h["_source"]["form"],
                     "filed": h["_source"]["file_date"]}
                    for h in d["hits"]["hits"]]
        except Exception:
            if i == 3:
                return []
            time.sleep(2.5)


def document_text(cik, adsh, doc):
    url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
           f"{adsh.replace('-', '')}/{doc}")
    raw = _get(url, timeout=180, raw=True).decode("utf8", "replace")
    stripped = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    return html.unescape(re.sub(r"<[^>]+>", " ", stripped))


def universe():
    seen = []
    for f in ("universe_largecap.txt", "universe_smallmid.txt"):
        for ln in (REPO / "src/shortlist/backtest" / f).read_text().splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                seen.append(ln.split()[0].upper())
    return sorted(set(seen))


def main():
    tickers = universe()
    m = _get("https://www.sec.gov/files/company_tickers.json")
    t2c = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in m.values()}
    pairs = [(t, t2c[t]) for t in tickers if t in t2c]
    print(f"universe {len(tickers)} symbols, {len(pairs)} resolvable to a CIK", flush=True)

    narrow_hits, net_hits, meta = collections.defaultdict(set), collections.defaultdict(set), {}
    t0 = time.time()
    for k, (tk, cik) in enumerate(pairs):
        for p in NARROW:
            for h in fts(p, cik):
                narrow_hits[tk].add(h["adsh"]); meta[(tk, h["adsh"])] = h
            time.sleep(0.12)
        for p in NETS:
            for h in fts(p, cik):
                net_hits[tk].add(h["adsh"]); meta[(tk, h["adsh"])] = h
            time.sleep(0.12)
        if k % 25 == 0:
            print(f"  fts {k}/{len(pairs)}  {time.time()-t0:.0f}s  "
                  f"narrow={len(narrow_hits)} nets={len(net_hits)}", flush=True)

    cand = sorted({(tk, a) for tk, s in narrow_hits.items() for a in s} |
                  {(tk, a) for tk, s in net_hits.items() for a in s})
    print(f"\nFTS done in {time.time()-t0:.0f}s: {len(narrow_hits)} tickers hit a narrow "
          f"phrase, {len(net_hits)} hit a net; {len(cand)} candidate filings to download\n",
          flush=True)

    rows = []
    for k, (tk, adsh) in enumerate(cand):
        h = meta[(tk, adsh)]
        if h["form"] != "10-Q":
            continue
        try:
            text = document_text(t2c[tk], adsh, h["doc"])
        except Exception as e:
            print(f"  !! {tk} {adsh} download failed: {type(e).__name__}", flush=True)
            continue
        period = None
        if h["period"]:
            try:
                period = datetime.date.fromisoformat(h["period"])
            except ValueError:
                pass
        shipped = controls.detect(text, period, CFG, form="10-Q", accession=adsh)
        saved, controls._SELF_REF = controls._SELF_REF, WIDE_SELF_REF
        widened = controls.detect(text, period, CFG, form="10-Q", accession=adsh)
        controls._SELF_REF = saved
        rows.append({
            "ticker": tk, "accession": adsh, "period": h["period"], "filed": h["filed"],
            "narrow": adsh in narrow_hits.get(tk, ()), "net": adsh in net_hits.get(tk, ()),
            "shipped": None if shipped is None else
                       {"basis": shipped.basis, "as_of": shipped.as_of, "quote": shipped.quote},
            "widened": None if widened is None else
                       {"basis": widened.basis, "as_of": widened.as_of, "quote": widened.quote},
        })
        if k % 10 == 0:
            print(f"  doc {k}/{len(cand)}  {time.time()-t0:.0f}s", flush=True)
        time.sleep(0.25)

    (SP / "tenq_controls_hits.json").write_text(json.dumps(rows, indent=1))
    ship_t = {r["ticker"] for r in rows if r["shipped"]}
    wide_t = {r["ticker"] for r in rows if r["widened"]}
    print(f"\n=== 10-Q RESULT over {len(pairs)} resolvable tickers ===")
    print(f"candidate filings downloaded : {len(rows)}")
    print(f"tickers flagged, SHIPPED regex: {len(ship_t)}  {sorted(ship_t)}")
    print(f"tickers flagged, WIDENED regex: {len(wide_t)}  {sorted(wide_t)}")
    print(f"gained by widening            : {sorted(wide_t - ship_t)}")
    print(f"filings flagged shipped/widened: "
          f"{sum(1 for r in rows if r['shipped'])}/{sum(1 for r in rows if r['widened'])}")
    print(f"net-only tickers (recall check): "
          f"{sorted({r['ticker'] for r in rows if r['net'] and not r['narrow']} - wide_t)}")
    print(f"\nexcerpts -> {SP / 'tenq_controls_hits.json'}")


if __name__ == "__main__":
    main()
