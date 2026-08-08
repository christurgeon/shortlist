"""Reproducible replay behind `docs/audits/2026-08-07-wsb-novelty-rule.md`.

Compares candidate qualification rules for the `wsb_hype` scout originator over the daily
ApeWisdom board payloads the scout caches. Committed per the CLAUDE.md rule that a verdict
which moves live behaviour must be reproducible from the tracked tree.

Run:  python3 docs/audits/scripts/wsb_novelty_replay.py

**Inputs are COMMITTED**, under `docs/audits/raw-2026-08-07-wsb/` — 43 distilled boards and
a market-cap snapshot. An earlier revision read only `.cache/apewisdom/`, which is
gitignored: the script was in the tree, the evidence was not, and on a fresh clone it
exited without reproducing anything. That is exactly the failure the "commit the evidence"
rule exists to prevent. Live cache dirs are still read as a fallback/extension.

**Market caps are REAL, not a hand-written name list.** The first revision of this analysis
scored composition against a 25-name "mega-cap" set and reported 0%; joining to actual caps
shows 15% of the same emissions above $200B (CAT $387B, UNH $370B, GEV, ANET, NVO were all
absent from the list). Never reintroduce a hand list here.

DENOMINATOR CONVENTION (not interchangeable; both are reported):
  * per calendar day = emissions / days with enough prior history to evaluate
  * per emitting day = emissions / days that produced at least one emission
"""
from __future__ import annotations

import collections
import glob
import json
import os
import statistics
import sys

RAW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "raw-2026-08-07-wsb")
LIVE_CACHE_DIRS = ("/opt/shortlist/.cache/apewisdom", ".cache/apewisdom")

# Index and leveraged-index products denied by the shipped config (config.yaml:
# scout.wsb_hype.deny_list). Applied to EVERY rule so the comparison is like-for-like —
# an earlier revision applied it to none of them and so did not measure shipped behaviour.
DENY = {"SPY", "QQQ", "IWM", "VIX", "SPX", "DIA", "SOXL", "SOXS", "SPXL",
        "TQQQ", "SQQQ", "UVXY", "VOO", "GLD"}

MIN_PRIOR_BOARDS = 5
MEGA_CAP = 200e9          # "no informational edge available to anyone" threshold
BAND_LO, BAND_HI = 0.3e9, 10e9   # the band docs/audits/2026-07-26 says is missing


def load_history(extra_dirs=()):
    """-> ({day: {TICKER: row}}, sorted_days). Committed raw first, live cache extends."""
    hist: dict[str, dict] = {}
    raw = os.path.join(RAW_DIR, "boards.json")
    if os.path.exists(raw):
        with open(raw) as fh:
            blob = json.load(fh)
        for day, rows in blob.get("boards", {}).items():
            hist[day] = {r[0]: {"rank": r[1], "mentions": r[2], "mentions_24h_ago": r[3]}
                         for r in rows}
    files = {}
    for d in list(LIVE_CACHE_DIRS) + list(extra_dirs):
        for p in glob.glob(os.path.join(d, "*.json")):
            files[os.path.basename(p)[:-5]] = p
    for day, path in files.items():
        if day in hist:
            continue
        try:
            with open(path) as fh:
                rows = json.load(fh)["payload"].get("results") or []
        except (OSError, ValueError, KeyError):
            continue
        hist[day] = {r["ticker"].upper(): r for r in rows if r.get("ticker")}
    return hist, sorted(hist)


def load_caps():
    p = os.path.join(RAW_DIR, "market_caps.json")
    if os.path.exists(p):
        with open(p) as fh:
            return {k: v[0] for k, v in json.load(fh)["caps"].items() if v and v[0]}
    for p in glob.glob(".cache/nasdaq_universe/*.json"):
        with open(p) as fh:
            return {k: v[0] for k, v in json.load(fh).items() if v and v[0]}
    return {}


def cap_profile(tickers, caps):
    vals = [caps[t] for t in tickers if t in caps and caps[t] > 0]
    if not vals:
        return None
    return {
        "n": len(tickers), "capped": len(vals),
        "median": statistics.median(vals),
        "mega": sum(1 for c in vals if c >= MEGA_CAP) / len(vals) * 100,
        "band": sum(1 for c in vals if BAND_LO <= c <= BAND_HI) / len(vals) * 100,
    }


def current_rule(hist, days, day, min_mentions=30, min_delta=0.5, top_n=15):
    """Shipped velocity rule: mention floor + rising + >= +50% 24h delta."""
    hot = []
    for t, r in hist[day].items():
        if t in DENY:
            continue
        m, prev = r.get("mentions"), r.get("mentions_24h_ago")
        if m is None or not prev:
            continue
        delta = (m - prev) / prev
        if m >= min_mentions and m > prev and delta >= min_delta:
            hot.append((delta, t))
    hot.sort(reverse=True)
    return [t for _, t in hot[:top_n]]


def median_baseline_rule(hist, days, day, lookback=14, min_ratio=2.0,
                         min_mentions=30, top_n=15):
    """KILLED design: spike vs the ticker's own trailing median mention count."""
    i = days.index(day)
    prior = days[max(0, i - lookback):i]
    if len(prior) < MIN_PRIOR_BOARDS:
        return None
    floors = {d: min([r.get("mentions") or 0 for r in hist[d].values()] or [0]) for d in prior}
    hot = []
    for t, r in hist[day].items():
        m = r.get("mentions")
        if t in DENY or m is None or m < min_mentions:
            continue
        series = [(hist[d].get(t, {}).get("mentions") or floors[d]) for d in prior]
        base = statistics.median(series) if series else 0
        if base <= 0:
            continue
        if m / base >= min_ratio:
            hot.append((m / base, t))
    hot.sort(reverse=True)
    return [t for _, t in hot[:top_n]]


def novelty_rule(hist, days, day, lookback=14, max_regular_rank=50,
                 min_mentions=20, top_n=15):
    """SHIPPED design: emit only names that are NOT board regulars.

    Cross-checked against `shortlist.scout.wsb_novelty.qualify_board` — the two agree on
    every one of the 43 days.
    """
    i = days.index(day)
    prior = days[max(0, i - lookback):i]
    if len(prior) < MIN_PRIOR_BOARDS:
        return None, len(prior)
    best = {}
    for p in prior:
        for t, r in hist[p].items():
            rk = r.get("rank")
            if rk is not None:
                best[t] = min(best.get(t, 10**9), rk)
    picks = []
    for t, r in hist[day].items():
        m = r.get("mentions") or 0
        if m < min_mentions or t in DENY:
            continue
        if best.get(t, 10**9) > max_regular_rank:
            picks.append((m, t))
    picks.sort(reverse=True)
    return [t for _, t in picks[:top_n]], len(prior)


def tally(hist, days, subset, fn):
    cnt = collections.Counter()
    n = emitting = evaluated = 0
    for d in subset:
        out = fn(hist, days, d)
        if isinstance(out, tuple):
            out = out[0]
        if out is None:
            continue
        evaluated += 1
        if out:
            emitting += 1
            n += len(out)
        for t in out:
            cnt[t] += 1
    return n, emitting, evaluated, cnt


def main():
    hist, days = load_history(sys.argv[1:])
    caps = load_caps()
    if len(days) < 10:
        print(f"insufficient history ({len(days)} days) — cannot replay")
        return 1
    print(f"boards: {len(days)} days, {days[0]} -> {days[-1]}; "
          f"market caps for {len(caps)} symbols\n")

    print("=== 1. HEADLINE: composition, on REAL market caps ===")
    print(f"{'rule':>32} {'emis':>5} {'/cal':>6} {'/emit':>6} {'median cap':>11} "
          f"{'>=200B':>7} {'0.3-10B':>8}")
    print("-" * 82)
    emitted = {}
    for label, fn in (("current velocity (shipped)", current_rule),
                      ("median-baseline (KILLED)", median_baseline_rule),
                      ("rank-novelty (14/50/20)", novelty_rule)):
        n, em, ev, cnt = tally(hist, days, days, fn)
        emitted[label] = cnt
        names = list(cnt.elements())
        p = cap_profile(names, caps)
        cap_s = (f"${p['median']/1e9:>9.1f}B {p['mega']:>6.0f}% {p['band']:>7.0f}%"
                 if p else " " * 26)
        print(f"{label:>32} {n:>5} {n/max(1,ev):>6.1f} {n/max(1,em):>6.1f} {cap_s}")
    print("  (share columns are over emissions with a known cap; see 'capped' below)")
    for label, cnt in emitted.items():
        p = cap_profile(list(cnt.elements()), caps)
        if p:
            print(f"    {label:>30}: {p['capped']}/{p['n']} emissions had a cap")

    print("\n=== 2. WHY THE MEDIAN BASELINE FAILED: mega-cap counts are VOLATILE ===")
    for name in ("AAPL", "TSLA", "MSFT", "NVDA"):
        ratios = []
        for i, d in enumerate(days):
            if i < MIN_PRIOR_BOARDS:
                continue
            prior = days[max(0, i - 14):i]
            series = [s for s in (hist[p].get(name, {}).get("mentions") or 0 for p in prior) if s]
            m = hist[d].get(name, {}).get("mentions")
            if m and series and statistics.median(series) > 0:
                ratios.append(m / statistics.median(series))
        if ratios:
            over = sum(1 for r in ratios if r >= 2.0)
            print(f"   {name:>5}: median ratio {statistics.median(ratios):.2f}  "
                  f"over-2x on {over}/{len(ratios)} days ({over/len(ratios)*100:.0f}%)")

    print("\n=== 3. THE VELOCITY FILTER'S CONTRIBUTION (a cause, NOT 'the entire' bias) ===")
    both = absent_with_base = 0
    from datetime import date as _d
    for i in range(1, len(days)):
        d, pr = days[i], days[i - 1]
        if (_d.fromisoformat(d) - _d.fromisoformat(pr)).days != 1:
            continue
        for t, r in hist[d].items():
            on_prior, has_base = t in hist[pr], bool(r.get("mentions_24h_ago"))
            absent_with_base += (not on_prior) and has_base
            both += (not on_prior) and (not has_base)
    print(f"   board-absent AND no baseline (truly unemittable): {both}")
    print(f"   board-absent BUT carrying a baseline:             {absent_with_base}")
    print("   => ApeWisdom tracks ~784 tickers across 8 pages; we cache page 1 (top 100),")
    print("      so absence from OUR board does not imply absence of a 24h baseline.")
    nov, _ = tally(hist, days, days, novelty_rule)[3], None
    no_base = sum(1 for d in days
                  for t in (novelty_rule(hist, days, d)[0] or [])
                  if not hist[d].get(t, {}).get("mentions_24h_ago"))
    total_nov = sum(nov.values())
    print(f"   novelty emissions from the unemittable population: {no_base}/{total_nov} "
          f"({no_base/max(1,total_nov)*100:.0f}%)")

    print("\n=== 4. HOLDOUT SPLIT (mechanism check, not a generalisation test — see doc) ===")
    mid = len(days) // 2
    tune, hold = days[:mid], days[mid:]
    print(f"   tune={tune[0]}..{tune[-1]} ({len(tune)})  holdout={hold[0]}..{hold[-1]} ({len(hold)})")
    print(f"{'(look, rank, ment)':>20} {'TUNE >=200B':>13} {'HOLD >=200B':>13} {'HOLD /emit':>11}")
    print("-" * 62)
    for look in (14, 21):
        for wr in (30, 50, 75):
            for mm in (20, 30):
                def fn(h, d, day, _l=look, _w=wr, _m=mm):
                    return novelty_rule(h, d, day, _l, _w, _m)
                _, _, _, c1 = tally(hist, days, tune, fn)
                n2, e2, _, c2 = tally(hist, days, hold, fn)
                p1 = cap_profile(list(c1.elements()), caps)
                p2 = cap_profile(list(c2.elements()), caps)
                print(f"{str((look, wr, mm)):>20} {(p1['mega'] if p1 else 0):>12.0f}% "
                      f"{(p2['mega'] if p2 else 0):>12.0f}% {n2/max(1,e2):>11.1f}")

    print("\n=== 5. SPARSE-HISTORY SENSITIVITY (14/50/20) ===")
    buckets = collections.defaultdict(lambda: {"em": 0, "days": 0, "names": []})
    for d in days:
        out, npri = novelty_rule(hist, days, d)
        if out is None:
            continue
        b = "5-7" if npri <= 7 else ("8-10" if npri <= 10 else "11-14")
        buckets[b]["days"] += 1
        buckets[b]["em"] += len(out)
        buckets[b]["names"] += out
    print(f"{'prior boards':>13} {'days':>6} {'emis':>6} {'/cal day':>9} {'>=200B':>8}")
    print("-" * 48)
    for b in ("5-7", "8-10", "11-14"):
        v = buckets.get(b)
        if not v or not v["days"]:
            continue
        p = cap_profile(v["names"], caps)
        print(f"{b:>13} {v['days']:>6} {v['em']:>6} {v['em']/v['days']:>9.1f} "
              f"{(p['mega'] if p else 0):>7.0f}%")
    print("   thinner history => MORE permissive on volume (small n; see doc §5).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
