"""Track record of persisted `/deep` screening calls against SPY.

Evidence for docs/audits/2026-11-02-deep-call-track-record.md; every rule here is fixed by
that note's pre-registration. Run from the repo root:

    uv run python docs/audits/scripts/probe_call_track_record.py            # fetch missing prices
    uv run python docs/audits/scripts/probe_call_track_record.py --offline  # cache only

Reads `raw-2026-11-02-calls/records/*.json`, caches each Yahoo chart series (bars on or before
CUTOFF only) in `raw-2026-11-02-calls/prices/`, prints the note's tables and writes
`results.json` and `episodes.csv` beside them. Output depends only on the two folders.
"""
import csv
import json
import random
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean, median

import httpx

RAW = Path(__file__).resolve().parent.parent / "raw-2026-11-02-calls"
CUTOFF = date(2026, 10, 30)
MIN_SESSIONS = 21
HORIZONS = (21, 63)
BOOT_N = 10_000
SEED = 20261102
CLOSE_UTC_HOUR = 20  # 16:00 New York in daylight time; every as_of here is in EDT
BULLISH = {"STRONG_BUY", "BUY"}
BEARISH = {"AVOID", "STRONG_AVOID"}
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{t}?range=1y&interval=1d"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) shortlist/0.1"  # as data/sources/yahoo.py
# Bound to IPv4: on this VPS an IPv6 connect to Yahoo stalls about 40 s before falling back.


def group(stance):
    if stance in BULLISH:
        return "bullish"
    if stance in BEARISH:
        return "bearish"
    return "HOLD" if stance == "HOLD" else None


def fetch_series(ticker, offline):
    path = RAW / "prices" / f"{ticker}.json"
    if path.exists():
        return json.loads(path.read_text())
    if offline:
        return None
    for attempt in range(3):
        try:
            with httpx.Client(transport=httpx.HTTPTransport(local_address="0.0.0.0"),
                              headers={"User-Agent": UA}, timeout=20.0) as c:
                r = c.get(URL.format(t=ticker))
            if r.status_code == 404:
                return None
            r.raise_for_status()
            break
        except httpx.HTTPError as e:
            print(f"# fetch {ticker} attempt {attempt + 1}: {type(e).__name__}", file=sys.stderr)
            time.sleep(3 * (attempt + 1))
    else:
        return None
    time.sleep(1.0)
    res = (r.json().get("chart", {}).get("result") or [None])[0]
    if not res or not res.get("timestamp"):
        return None
    q = res["indicators"]["quote"][0]
    adj = res["indicators"]["adjclose"][0]["adjclose"]
    out = {"dates": [], "close": [], "adjclose": [], "high": [], "low": []}
    for i, ts in enumerate(res["timestamp"]):
        d = datetime.fromtimestamp(ts, timezone.utc).date()
        vals = (q["close"][i], adj[i], q["high"][i], q["low"][i])
        if d > CUTOFF or any(v is None for v in vals):
            continue
        out["dates"].append(d.isoformat())
        for k, v in zip(("close", "adjclose", "high", "low"), vals, strict=True):
            out[k].append(v)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return out


def index_by_date(s):
    return {d: i for i, d in enumerate(s["dates"])}


def entry_session(as_of, cal):
    """Index in `cal` of the last session whose close is at or before `as_of`."""
    best = None
    for i, d in enumerate(cal):
        close_at = datetime.fromisoformat(d).replace(hour=CLOSE_UTC_HOUR, tzinfo=timezone.utc)
        if close_at <= as_of:
            best = i
        else:
            break
    return best


def price_check(p, s, sidx, entry_d, as_of):
    """(agrees, note) for the brief's as_of_price against the unadjusted series. The
    session range is used only for a brief written during regular hours (an intraday quote)."""
    ce = s["close"][sidx[entry_d]]
    if abs(p / ce - 1) <= 0.02:
        return True, ""
    as_of_d = as_of.date().isoformat()
    intraday = (13, 30) <= (as_of.hour, as_of.minute) < (CLOSE_UTC_HOUR, 0)
    if intraday and as_of_d in sidx:
        j = sidx[as_of_d]
        if s["low"][j] * 0.99 <= p <= s["high"][j] * 1.01:
            return True, ""
    near = [d for d, c in zip(s["dates"], s["close"], strict=True) if d <= as_of_d and abs(p / c - 1) <= 0.005]
    stale = f"; close within 0.5% on {near[-1]}" if near else "; no close within 0.5% before as_of"
    return False, f"as_of_price {p:.2f} vs entry close {ce:.2f} (ratio {p / ce:.3f}){stale}"


def excess(s, sidx, spy, cal, xi, p0, b0):
    """Stock minus SPY adjusted return from entry to session `xi`; None if the stock has no bar."""
    xd = cal[xi]
    if xd not in sidx:
        return None
    return (s["adjclose"][sidx[xd]] / p0 - 1) - (spy["adjclose"][xi] / b0 - 1)


def bootstrap_spread(eps, key):
    """Bullish-minus-bearish mean `key` and its ticker-clustered 95% percentile interval."""
    eps = [e for e in eps if e[key] is not None and e["grp"] in ("bullish", "bearish")]
    by_t = defaultdict(list)
    for e in eps:
        by_t[e["ticker"]].append(e)
    tickers = sorted(by_t)
    bull = [e[key] for e in eps if e["grp"] == "bullish"]
    bear = [e[key] for e in eps if e["grp"] == "bearish"]
    if not bull or not bear:
        return None
    point = mean(bull) - mean(bear)
    rng = random.Random(SEED)
    draws, redraws = [], 0
    while len(draws) < BOOT_N:
        b, a = [], []
        for t in (rng.choice(tickers) for _ in tickers):
            for e in by_t[t]:
                (b if e["grp"] == "bullish" else a).append(e[key])
        if not b or not a:
            redraws += 1
            continue
        draws.append(mean(b) - mean(a))
    draws.sort()
    lo, hi = draws[round(0.025 * (BOOT_N - 1))], draws[round(0.975 * (BOOT_N - 1))]
    med = median(bull) - median(bear)
    return {"spread": point, "lo": lo, "hi": hi, "redraws": redraws, "median_spread": med,
            "n_bull": len(bull), "n_bear": len(bear), "tickers": len(tickers)}


def dedupe(recs, keyname):
    out, last = [], {}
    for r in sorted(recs, key=lambda r: (r["ticker"], r["as_of"])):
        k = r[keyname]
        if k is None:
            continue
        if last.get(r["ticker"]) != k:
            out.append(dict(r))
            last[r["ticker"]] = k
    return out


def group_table(eps, keys):
    rows = []
    for g in ("bullish", "HOLD", "bearish"):
        for key in keys:
            xs = [e[key] for e in eps if e["grp"] == g and e[key] is not None]
            if not xs:
                rows.append({"group": g, "horizon": key, "n": 0})
                continue
            hit = (sum(x > 0 for x in xs) if g != "bearish" else sum(x < 0 for x in xs)) / len(xs)
            rows.append({"group": g, "horizon": key, "n": len(xs),
                         "tickers": len({e["ticker"] for e in eps if e["grp"] == g and e[key] is not None}),
                         "mean": mean(xs), "median": median(xs), "hit": hit})
    return rows


def pct(x):
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def main():
    offline = "--offline" in sys.argv
    recs = [json.loads(p.read_text()) for p in sorted((RAW / "records").glob("*.json"))]
    spy = fetch_series("SPY", offline)
    if spy is None:
        sys.exit("no SPY series")
    cal = spy["dates"]
    spy_idx = index_by_date(spy)
    last_i = len(cal) - 1
    assert cal[-1] == CUTOFF.isoformat(), cal[-1]

    excluded, flags, eligible = [], [], []
    for r in recs:
        sc = r["screening_call"]
        tag = f"{r['ticker']} {r['as_of'][:10]}"
        if not sc:
            excluded.append((tag, "no screening_call"))
            continue
        if sc.get("as_of_price") is None:
            excluded.append((tag, "no as_of_price"))
            continue
        as_of = datetime.fromisoformat(r["as_of"])
        ei = entry_session(as_of, cal)
        if ei is None or last_i - ei < MIN_SESSIONS:
            excluded.append((tag, f"younger than {MIN_SESSIONS} sessions"
                             f" ({last_i - ei if ei is not None else '?'})"))
            continue
        s = fetch_series(r["ticker"], offline)
        if s is None or not s["dates"]:
            excluded.append((tag, "no Yahoo series"))
            continue
        sidx = index_by_date(s)
        entry_d = cal[ei]
        if entry_d not in sidx:
            excluded.append((tag, f"no stock bar on entry session {entry_d}"))
            continue
        ok, note = price_check(sc["as_of_price"], s, sidx, entry_d, as_of)
        if not ok:
            flags.append((tag, note))
        if s["dates"][-1] != cal[-1]:
            flags.append((tag, f"series ends {s['dates'][-1]}, before the cut-off"))
        p0, b0 = s["adjclose"][sidx[entry_d]], spy["adjclose"][spy_idx[entry_d]]

        if sc.get("model_stance"):
            pre = sc["model_stance"]
        elif not sc.get("stance_clamped"):
            pre = sc["stance"]
        else:
            pre = None
        rec = {"ticker": r["ticker"], "as_of": r["as_of"], "model": r["model"],
               "stance": sc["stance"], "pre_stance": pre, "conviction": sc["conviction"],
               "stance_clamped": bool(sc.get("stance_clamped")),
               "conviction_capped": bool(sc.get("conviction_capped")),
               "entry": entry_d, "price_flag": not ok, "sessions": last_i - ei}
        for h in HORIZONS:
            rec[f"x{h}"] = excess(s, sidx, spy, cal, ei + h, p0, b0) if ei + h <= last_i else None
        rec["xlast"] = excess(s, sidx, spy, cal, last_i, p0, b0)
        # Post hoc, not pre-registered: enter at the first close after as_of, so a move the
        # brief already saw intraday is not credited to the call.
        ni = ei + 1
        rec["x21n"] = rec["xlastn"] = None
        if cal[ni] in sidx:
            p0n, b0n = s["adjclose"][sidx[cal[ni]]], spy["adjclose"][ni]
            if ni + MIN_SESSIONS <= last_i:
                rec["x21n"] = excess(s, sidx, spy, cal, ni + MIN_SESSIONS, p0n, b0n)
            rec["xlastn"] = excess(s, sidx, spy, cal, last_i, p0n, b0n)
        eligible.append(rec)

    keys = [f"x{h}" for h in HORIZONS] + ["xlast"]
    results = {"cutoff": CUTOFF.isoformat(), "records": len(recs), "eligible": len(eligible),
               "excluded": excluded, "flags": flags}

    print(f"records {len(recs)}, eligible {len(eligible)} on "
          f"{len({e['ticker'] for e in eligible})} tickers, excluded {len(excluded)}")
    print(f"models: {dict(Counter(e['model'] for e in eligible))}")
    print("\n## Exclusions")
    for reason, n in Counter(x[1].split(' (')[0] for x in excluded).most_common():
        print(f"- {reason}: {n}")
    for tag, why in excluded:
        if not why.startswith("younger"):
            print(f"  - {tag}: {why}")
    print("\n## Flags")
    for tag, why in flags:
        print(f"- {tag}: {why}")
    if not flags:
        print("- none")

    for label, keyname in (("primary (stance shown)", "stance"), ("secondary (pre-clamp)", "pre_stance")):
        eps = dedupe(eligible, keyname)
        for e in eps:
            e["grp"] = group(e[keyname])
        sec = {"episodes": len(eps), "table": group_table(eps, keys), "spreads": {}}
        print(f"\n## {label}: {len(eps)} episodes on {len({e['ticker'] for e in eps})} tickers")
        print("| group | horizon | n | tickers | mean excess | median excess | hit rate |")
        print("|---|---|---|---|---|---|---|")
        for row in sec["table"]:
            if row["n"] == 0:
                print(f"| {row['group']} | {row['horizon']} | 0 | | | | |")
                continue
            print(f"| {row['group']} | {row['horizon']} | {row['n']} | {row['tickers']} | "
                  f"{pct(row['mean'])} | {pct(row['median'])} | {row['hit'] * 100:.0f}% |")
        print("\n| horizon | bullish n | bearish n | tickers | spread (mean) | 95% CI | median spread | redraws |")
        print("|---|---|---|---|---|---|---|---|")
        for key in keys:
            b = bootstrap_spread(eps, key)
            sec["spreads"][key] = b
            if b is None:
                print(f"| {key} | | | | n/a | | | |")
                continue
            print(f"| {key} | {b['n_bull']} | {b['n_bear']} | {b['tickers']} | {pct(b['spread'])} | "
                  f"[{pct(b['lo'])}, {pct(b['hi'])}] | {pct(b['median_spread'])} | {b['redraws']} |")
        results[keyname] = sec
        if keyname == "stance":
            primary = eps

    clean = [e for e in eligible if not e["price_flag"]]
    eps_clean = dedupe(clean, "stance")
    for e in eps_clean:
        e["grp"] = group(e["stance"])
    b = bootstrap_spread(eps_clean, "x21")
    results["sensitivity_no_flagged"] = b
    print("\n## Sensitivity: flagged as_of_price records dropped (x21)")
    print("n/a" if b is None else
          f"bullish {b['n_bull']}, bearish {b['n_bear']}: spread {pct(b['spread'])} "
          f"[{pct(b['lo'])}, {pct(b['hi'])}]")

    print("\n## Post hoc: entry at the first close after as_of (primary grouping)")
    results["post_hoc_next_close"] = {}
    for key in ("x21n", "xlastn"):
        b = bootstrap_spread(primary, key)
        results["post_hoc_next_close"][key] = b
        print(f"- {key}: bullish {b['n_bull']}, bearish {b['n_bear']}: spread {pct(b['spread'])} "
              f"[{pct(b['lo'])}, {pct(b['hi'])}]")

    print("\n## Episodes, bullish and bearish (primary)")
    print("| ticker | as_of | model | stance | conviction | entry | x21 | x63 | to 10-30 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for e in sorted(primary, key=lambda e: (e["grp"], e["as_of"])):
        if e["grp"] in ("bullish", "bearish"):
            print(f"| {e['ticker']} | {e['as_of'][:10]} | {e['model'].removeprefix('claude-')} | "
                  f"{e['stance']} | {e['conviction']} | {e['entry']} | {pct(e['x21'])} | "
                  f"{pct(e['x63'])} | {pct(e['xlast'])} |")

    print("\n## Descriptive: all calls with a screening_call")
    calls = [r["screening_call"] for r in recs if r["screening_call"]]
    print(f"calls {len(calls)}; stance {dict(Counter(c['stance'] for c in calls).most_common())}")
    print(f"conviction {dict(Counter(c['conviction'] for c in calls).most_common())}")
    print(f"stance clamped {sum(bool(c.get('stance_clamped')) for c in calls)}: "
          f"{dict(Counter(c.get('clamp_note') for c in calls if c.get('stance_clamped')).most_common())}")
    print(f"model_stance persisted on clamped: "
          f"{sum(bool(c.get('model_stance')) for c in calls if c.get('stance_clamped'))}")
    print(f"conviction capped {sum(bool(c.get('conviction_capped')) for c in calls)}")
    print("\n## Descriptive: conviction vs mean excess (primary episodes)")
    print("| group | conviction | n x21 | mean x21 | n to 10-30 | mean to 10-30 |")
    print("|---|---|---|---|---|---|")
    for g in ("bullish", "HOLD", "bearish"):
        for cv in ("HIGH", "MEDIUM", "LOW"):
            sel = [e for e in primary if e["grp"] == g and e["conviction"] == cv]
            if not sel:
                continue
            a = [e["x21"] for e in sel if e["x21"] is not None]
            z = [e["xlast"] for e in sel if e["xlast"] is not None]
            print(f"| {g} | {cv} | {len(a)} | {pct(mean(a)) if a else 'n/a'} | {len(z)} | "
                  f"{pct(mean(z)) if z else 'n/a'} |")

    (RAW / "results.json").write_text(json.dumps(results, indent=1, sort_keys=True, default=str))
    with open(RAW / "episodes.csv", "w", newline="") as f:
        cols = ["ticker", "as_of", "model", "stance", "pre_stance", "conviction", "stance_clamped",
                "conviction_capped", "entry", "sessions", "price_flag", "x21", "x63", "xlast",
                "x21n", "xlastn"]
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for e in sorted(eligible, key=lambda e: (e["ticker"], e["as_of"])):
            w.writerow({k: (f"{e[k]:.6f}" if isinstance(e[k], float) else e[k]) for k in cols})


if __name__ == "__main__":
    main()
