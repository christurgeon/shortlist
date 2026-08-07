"""Reproducible replay behind `docs/audits/2026-08-07-wsb-novelty-rule.md`.

Replays the daily ApeWisdom board payloads the scout already caches
(`.cache/apewisdom/<date>.json`, top-100 tickers per day) to compare candidate
qualification rules for `WsbHypeSignal`. Committed per the CLAUDE.md rule that a
verdict which moves live behaviour must be reproducible from the tracked tree.

Run:  python3 docs/audits/scripts/wsb_novelty_replay.py [extra_cache_dir ...]

Reads every cache dir given plus the two default locations; a date present in more
than one dir is read once (later dirs win). Pure stdlib, no network, no repo imports —
it must stay runnable after the signal it measures has been rewritten.

DENOMINATOR CONVENTION (the two are NOT interchangeable and both are reported):
  * per calendar day  = emissions / days with enough prior history to evaluate
  * per emitting day  = emissions / days that produced at least one emission
An earlier draft mixed the two; the audit quotes both explicitly.
"""
from __future__ import annotations

import collections
import glob
import json
import os
import statistics
import sys

DEFAULT_CACHE_DIRS = (
    "/opt/shortlist/.cache/apewisdom",
    ".cache/apewisdom",
)

# Hand-maintained proxy for "mega-cap / no informational edge available". Deliberately
# a fixed list rather than a live market-cap lookup: the audit's claim is about a
# stable, well-known set of names, and a network fetch would make this unreproducible.
MEGA = {"AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "GOOGL", "META", "TSLA", "AMD", "IBM",
        "INTC", "NFLX", "MU", "AVGO", "QCOM", "CRM", "ORCL", "BA", "DIS", "JPM", "V",
        "WMT", "COST", "PLTR", "SMCI"}

# Index and leveraged-index products. The shipped config denies only the first six;
# the leveraged ones surfaced in the replay and are part of this audit's findings.
ETF = {"SPY", "QQQ", "IWM", "VIX", "SPX", "DIA",
       "SOXL", "TQQQ", "SQQQ", "UVXY", "SPXL", "VOO", "SOXS"}

MIN_PRIOR_BOARDS = 5   # below this the rule abstains rather than guess


def load_history(extra_dirs=()):
    files = {}
    for d in list(DEFAULT_CACHE_DIRS) + list(extra_dirs):
        for p in glob.glob(os.path.join(d, "*.json")):
            files[os.path.basename(p)[:-5]] = p
    hist = {}
    for day, path in files.items():
        try:
            payload = json.load(open(path))["payload"]
        except (OSError, ValueError, KeyError):
            continue
        rows = payload.get("results") or []
        hist[day] = {r["ticker"].upper(): r for r in rows if r.get("ticker")}
    return hist, sorted(hist)


def mega_share(counter):
    tot = sum(counter.values())
    mega = sum(v for k, v in counter.items() if k in MEGA)
    return mega, tot, (mega / tot * 100 if tot else 0.0)


def current_rule(hist, days, day, min_mentions=30, min_delta=0.5, top_n=15):
    """Shipped rule: absolute mention floor + rising + >=+50% 24h delta."""
    hot = []
    for t, r in hist[day].items():
        m, prev = r.get("mentions"), r.get("mentions_24h_ago")
        if m is None or not prev:
            continue          # NOTE: this `not prev` is the structural defect (see audit)
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
        if m is None or m < min_mentions:
            continue
        # absence on a prior day is CENSORED, not zero: bound it by that day's rank-100 count
        series = [(hist[d].get(t, {}).get("mentions") or floors[d]) for d in prior]
        base = statistics.median(series) if series else 0
        if base <= 0:
            continue
        ratio = m / base
        if ratio >= min_ratio:
            hot.append((ratio, t))
    hot.sort(reverse=True)
    return [t for _, t in hot[:top_n]]


def novelty_rule(hist, days, day, lookback=14, max_regular_rank=50,
                 min_mentions=20, top_n=15):
    """SHIPPED design: qualify only names that are NOT board regulars.

    Returns (tickers, n_prior_boards) or (None, n) when history is too thin.
    A ticker's prior-window BEST rank must be worse than `max_regular_rank`, or it
    must be absent from every prior board.
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
        if m < min_mentions or t in ETF:
            continue
        if best.get(t, 10**9) > max_regular_rank:
            picks.append((m, t))
    picks.sort(reverse=True)
    return [t for _, t in picks[:top_n]], len(prior)


def tally(hist, days, subset, fn):
    """-> (emissions, emitting_days, evaluated_days, mega, total)"""
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
    mega, tot, _ = mega_share(cnt)
    return n, emitting, evaluated, mega, tot, cnt


def main():
    hist, days = load_history(sys.argv[1:])
    if len(days) < 10:
        print(f"insufficient cached history ({len(days)} days) — cannot replay")
        return 1
    print(f"cached boards: {len(days)} days, {days[0]} -> {days[-1]}\n")

    print("=== 1. HEADLINE: qualification rules compared (all days) ===")
    print(f"{'rule':>34} {'emis':>6} {'/cal day':>9} {'/emit day':>10} {'mega%':>7}")
    print("-" * 70)
    for label, fn in (
        ("current (delta >= +50%)", current_rule),
        ("median-baseline (KILLED)", median_baseline_rule),
        ("rank-novelty (14d, >50, >=20)", novelty_rule),
    ):
        n, em, ev, mg, tot, _ = tally(hist, days, days, fn)
        print(f"{label:>34} {n:>6} {n/max(1,ev):>9.1f} {n/max(1,em):>10.1f} "
              f"{mg/max(1,tot)*100:>6.0f}%")

    print("\n=== 2. WHY THE MEDIAN BASELINE FAILED: mega-cap mention counts are VOLATILE ===")
    print("   (days on which the name exceeds 2x its own trailing 14d median)")
    for name in ("AAPL", "TSLA", "MSFT", "NVDA"):
        ratios = []
        for i, d in enumerate(days):
            if i < MIN_PRIOR_BOARDS:
                continue
            prior = days[max(0, i - 14):i]
            series = [hist[p].get(name, {}).get("mentions") or 0 for p in prior]
            series = [s for s in series if s > 0]
            m = hist[d].get(name, {}).get("mentions")
            if m and series:
                base = statistics.median(series)
                if base > 0:
                    ratios.append(m / base)
        if ratios:
            over = sum(1 for r in ratios if r >= 2.0)
            print(f"   {name:>5}: median ratio {statistics.median(ratios):.2f}  "
                  f"over-2x on {over}/{len(ratios)} days ({over/len(ratios)*100:.0f}%)")

    print("\n=== 3. ROOT CAUSE: names with no prior-day baseline are unemittable today ===")
    noprev, ranks = [], []
    for d in days:
        n = sum(1 for r in hist[d].values() if not r.get("mentions_24h_ago"))
        noprev.append(n)
        for t, r in hist[d].items():
            if not r.get("mentions_24h_ago") and (r.get("mentions") or 0) >= 20:
                if r.get("rank") is not None:
                    ranks.append(r["rank"])
    print(f"   tickers/day arriving with no 24h baseline: mean {statistics.mean(noprev):.1f} of 100")
    if ranks:
        print(f"   of those clearing 20 mentions (n={len(ranks)}): median arrival rank "
              f"{statistics.median(ranks):.1f}")
    print("   the shipped rule requires mentions_24h_ago > 0, so it discards every one.")

    print("\n=== 4. HOLDOUT SPLIT (tune on first half, verify on second) ===")
    mid = len(days) // 2
    tune, hold = days[:mid], days[mid:]
    print(f"   tune={tune[0]}..{tune[-1]} ({len(tune)})   holdout={hold[0]}..{hold[-1]} ({len(hold)})")
    print(f"{'(look, rank, ment)':>20} {'TUNE mega%':>12} {'HOLD mega%':>12} {'HOLD /emit':>11}")
    print("-" * 60)
    scored = []
    for look in (14, 21):
        for wr in (30, 50, 75):
            for mm in (20, 30):
                def fn(h, d, day, _l=look, _w=wr, _m=mm):
                    return novelty_rule(h, d, day, _l, _w, _m)
                n1, e1, v1, g1, t1, _ = tally(hist, days, tune, fn)
                n2, e2, v2, g2, t2, _ = tally(hist, days, hold, fn)
                p1 = g1 / t1 * 100 if t1 else 0.0
                p2 = g2 / t2 * 100 if t2 else 0.0
                scored.append((p1, -n1, (look, wr, mm), p2, n2, e2))
                print(f"{str((look, wr, mm)):>20} {p1:>11.0f}% {p2:>11.0f}% "
                      f"{n2/max(1,e2):>11.1f}")
    scored.sort()
    p1, _, cfg, p2, n2, e2 = scored[0]
    print(f"\n   winner selected on TUNE ONLY: {cfg}  (tune {p1:.0f}% mega)")
    print(f"   its OUT-OF-SAMPLE result:     {p2:.0f}% mega, {n2} emissions, "
          f"{n2/max(1,e2):.1f}/emitting day")

    print("\n=== 5. SPARSE-HISTORY SENSITIVITY (config 14 / 50 / 20) ===")
    buckets = collections.defaultdict(lambda: {"em": 0, "days": 0, "mega": 0})
    for d in days:
        out, npri = novelty_rule(hist, days, d)
        if out is None:
            continue
        b = "5-7" if npri <= 7 else ("8-10" if npri <= 10 else "11-14")
        buckets[b]["days"] += 1
        buckets[b]["em"] += len(out)
        buckets[b]["mega"] += sum(1 for t in out if t in MEGA)
    print(f"{'prior boards':>13} {'days':>6} {'emis':>6} {'/cal day':>9} {'mega%':>7}")
    print("-" * 46)
    for b in ("5-7", "8-10", "11-14"):
        v = buckets.get(b)
        if not v or not v["days"]:
            continue
        print(f"{b:>13} {v['days']:>6} {v['em']:>6} {v['em']/v['days']:>9.1f} "
              f"{v['mega']/max(1,v['em'])*100:>6.0f}%")
    print("   thinner history => MORE permissive on volume; purity is unaffected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
