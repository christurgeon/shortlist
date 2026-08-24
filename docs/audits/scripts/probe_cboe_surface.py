#!/usr/bin/env python3
"""Reproduce the CBOE options-surface evidence in `2026-08-24-options-surface-design.md`.

Measures, over both committed universes: feed liveness/coverage, payload cost, quote
quality, the quote-quality guard pass rates, and the 25-delta skew reference distribution
the design's context line prints (§6.3). That reference is REGIME-DEPENDENT — re-run this
when it is more than ~6 months old and record the prevailing macro line with it.

Two measured traps this script exists to demonstrate, both from the design doc:

  §4.1  Cloudflare enforces a rolling per-IP budget. A cookie-less loop at 0.25s died at 60
        requests with no recovery; a cookie-persisting httpx client at 0.5s reached 72+.
        Hence one client, cookies persisted, `--delay` never below 0.5.
  §4.3  Expired contracts stay in the file (up to 260 on one name), which is what produced
        a nonsense 82% "implied move" before the expiry filter existed.

Usage:
    uv run python docs/audits/scripts/probe_cboe_surface.py            # both universes
    uv run python docs/audits/scripts/probe_cboe_surface.py --limit 20 --universe largecap

Writes one JSON record per ticker to --out (default: cboe_surface_probe.jsonl) and prints
the summary tables. Read-only; touches nothing under state/ or data/.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import statistics as st
from pathlib import Path
from typing import Any, Optional

URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{}.json"
UNIVERSE_DIR = Path(__file__).resolve().parents[3] / "src" / "shortlist" / "backtest"
DELTA_TARGET = 0.25
DELTA_TOLERANCE = 0.10      # config default: research.options.delta_tolerance
MAX_ATM_SPREAD_PCT = 40.0   # config default: research.options.max_atm_spread_pct


def read_universe(name: str) -> list[str]:
    out: list[str] = []
    for line in (UNIVERSE_DIR / f"universe_{name}.txt").read_text().splitlines():
        line = line.split("#")[0].strip()
        if line:
            out.append(line.split(",")[0].strip())
    return out


def parse_osi(symbol: str, root: str) -> tuple[datetime.date, str, float]:
    """OSI contract symbol -> (expiry, 'C'|'P', strike). Root is the file's own
    `data.symbol`, which is not always the ticker we requested."""
    body = symbol[len(root):]
    return (datetime.date(2000 + int(body[:2]), int(body[2:4]), int(body[4:6])),
            body[6], int(body[7:]) / 1000.0)


def _tradeable(o: dict) -> bool:
    """A quote we are willing to read: two-sided AND a positive IV. AAPL carries a
    deep-ITM strike at iv 2.0684 (207%) on delta 0.9998 — a stale one-sided quote
    inverting to a meaningless implied vol (design §4.4)."""
    return bool(o.get("bid")) and bool(o.get("ask")) and bool(o.get("iv"))


def _pick(rows: list[tuple[float, dict]], target: float) -> Optional[tuple[float, dict]]:
    """Nearest contract to a target delta, or None when the nearest still misses by more
    than the tolerance. Rejecting on the ACHIEVED delta is the guard that matters: RES
    produced a 77-vol-point skew from a put at delta -0.888 against a call at 0.869,
    because only two or three contracts per side carried usable quotes (design §6.4)."""
    if not rows:
        return None
    best = min(rows, key=lambda r: abs(abs(r[0]) - target))
    return best if abs(abs(best[0]) - target) <= DELTA_TOLERANCE else None


def analyse(ticker: str, payload: dict, today: datetime.date) -> dict:
    data = payload.get("data") or {}
    root = data.get("symbol") or ticker
    spot = data.get("current_price")
    rec: dict[str, Any] = {
        "spot": spot, "iv30": data.get("iv30"),
        "quote_time": data.get("last_trade_time"),   # the honest staleness anchor (§4.2)
        "file_timestamp": payload.get("timestamp"),  # when CBOE rebuilt the JSON; NOT quote time
        "n_contracts": len(data.get("options") or []),
    }
    by_expiry: dict[datetime.date, list] = {}
    expired = 0
    for o in data.get("options") or []:
        try:
            expiry, right, strike = parse_osi(o["option"], root)
        except (ValueError, KeyError, IndexError):
            continue
        if expiry < today:          # §4.3 — the file retains expired contracts
            expired += 1
            continue
        by_expiry.setdefault(expiry, []).append((right, strike, o))
    rec["expired_contracts"] = expired
    rec["live_expiries"] = len(by_expiry)
    if not by_expiry or not spot:
        return rec

    expiries = sorted(by_expiry)
    ref = min(expiries, key=lambda e: abs((e - today).days - 30))
    rec["ref_expiry"], rec["ref_dte"] = str(ref), (ref - today).days
    rows = by_expiry[ref]
    puts = [(o["delta"], o) for r, _, o in rows if r == "P" and _tradeable(o) and o.get("delta")]
    calls = [(o["delta"], o) for r, _, o in rows if r == "C" and _tradeable(o) and o.get("delta")]
    rec["liquid_puts"], rec["liquid_calls"] = len(puts), len(calls)

    p25, c25 = _pick(puts, DELTA_TARGET), _pick(calls, DELTA_TARGET)
    rec["delta_guard_pass"] = bool(p25 and c25)
    if p25 and c25:
        rec["p25_delta"], rec["c25_delta"] = round(p25[0], 3), round(c25[0], 3)
        rec["skew_pts"] = round((p25[1]["iv"] - c25[1]["iv"]) * 100, 2)

    atm_p, atm_c = _pick(puts, 0.50), _pick(calls, 0.50)
    if atm_p and atm_c:
        mid = lambda o: (o["bid"] + o["ask"]) / 2                       # noqa: E731
        mc, mp = mid(atm_c[1]), mid(atm_p[1])
        if mc:
            rec["atm_spread_pct"] = round((atm_c[1]["ask"] - atm_c[1]["bid"]) / mc * 100, 1)
        rec["atm_iv"] = round((atm_c[1]["iv"] + atm_p[1]["iv"]) / 2, 4)
        rec["straddle_pct"] = round((mc + mp) / spot * 100, 2)
        rec["spread_guard_pass"] = (rec.get("atm_spread_pct") or 999) <= MAX_ATM_SPREAD_PCT
    return rec


async def collect(names: list[tuple[str, str]], delay: float, out_path: Path) -> list[dict]:
    import httpx
    today = datetime.date.today()
    identity = os.environ.get("SEC_IDENTITY", "contact unset")
    headers = {"User-Agent": f"shortlist options-surface probe ({identity})",
               "Accept": "application/json"}
    records: list[dict] = []
    # ONE client for the whole run: cookies persist, which is what keeps Cloudflare's bot
    # management from treating each request as a new client (design §4.1).
    async with httpx.AsyncClient(timeout=30.0, headers=headers, follow_redirects=True) as client:
        with out_path.open("w") as fh:
            for universe, ticker in names:
                rec: dict[str, Any] = {"ticker": ticker, "universe": universe}
                try:
                    r = await client.get(URL.format(ticker))
                    rec["status"], rec["bytes"] = r.status_code, len(r.content)
                    if r.status_code == 200:
                        rec.update(analyse(ticker, r.json(), today))
                    elif r.status_code == 429:
                        print(f"  {ticker}: 429 — per-IP budget reached; "
                              f"stop and retry later (design §4.1)")
                except Exception as e:                       # noqa: BLE001 — a probe reports, never aborts
                    rec["status"] = f"ERR:{type(e).__name__}"
                records.append(rec)
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
                await asyncio.sleep(delay)
    return records


def _quantiles(values: list[float]) -> str:
    v = sorted(values)
    if len(v) < 5:      # below this the percentile indices collapse onto the same points
        return f"n={len(v)} (too few for quantiles): " + ", ".join(f"{x:+.1f}" for x in v)
    q = lambda p: v[int(p * (len(v) - 1))]                    # noqa: E731
    return (f"min {v[0]:+.2f}  p10 {q(.10):+.2f}  p25 {q(.25):+.2f}  MED {q(.50):+.2f}  "
            f"p75 {q(.75):+.2f}  p90 {q(.90):+.2f}  max {v[-1]:+.2f}")


def summarize(records: list[dict]) -> None:
    ok = [r for r in records if r.get("status") == 200]
    print(f"\nfetched {len(ok)} of {len(records)} "
          f"({sum(1 for r in records if r.get('status') == 429)} rate-limited)")
    for universe in ("largecap", "smallmid"):
        rows = [r for r in ok if r["universe"] == universe and r.get("live_expiries")]
        if not rows:
            continue
        delta_ok = [r for r in rows if r.get("delta_guard_pass")]
        both_ok = [r for r in delta_ok if r.get("spread_guard_pass")]
        print(f"\n=== {universe.upper()} (n={len(rows)}) ===")
        print(f"  payload      median {st.median([r['bytes'] for r in rows]) / 1e3:,.0f} KB  "
              f"max {max(r['bytes'] for r in rows) / 1e3:,.0f} KB")
        print(f"  live expiries median {st.median([r['live_expiries'] for r in rows]):.0f}")
        print(f"  files with expired contracts: "
              f"{sum(1 for r in rows if r.get('expired_contracts'))} of {len(rows)} "
              f"(max {max(r.get('expired_contracts', 0) for r in rows)})")
        print(f"  delta guard  {len(delta_ok)} of {len(rows)} "
              f"({100 * len(delta_ok) / len(rows):.0f}%)  -> gates skew + term slope")
        print(f"  + spread     {len(both_ok)} of {len(rows)} "
              f"({100 * len(both_ok) / len(rows):.0f}%)  -> gates the implied move")
        spreads = [r["atm_spread_pct"] for r in rows if r.get("atm_spread_pct") is not None]
        if spreads:
            print(f"  ATM spread % of mid: median {st.median(spreads):.1f}  "
                  f"p90 {sorted(spreads)[int(.9 * (len(spreads) - 1))]:.1f}")
        skews = [r["skew_pts"] for r in delta_ok if r.get("skew_pts") is not None]
        if skews:
            print(f"  25-delta skew (vol pts, guarded): {_quantiles(skews)}")
            print(f"    negative (calls bid over puts): "
                  f"{sum(1 for s in skews if s < 0)} of {len(skews)}")
        moves = [r["straddle_pct"] for r in both_ok if r.get("straddle_pct")]
        if moves:
            m = sorted(moves)
            print(f"  implied move ~30d: p10 {m[int(.1 * (len(m) - 1))]:.1f}%  "
                  f"MED {st.median(m):.1f}%  p90 {m[int(.9 * (len(m) - 1))]:.1f}%")
    print("\nThe LARGECAP guarded skew row above is the reference distribution the "
          "context line prints (design §6.3). It is regime-dependent — record the macro "
          "line alongside any re-measure.")


async def realized_reference(records: list[dict]) -> None:
    """IV30 / realized-vol reference distribution (design §6.1).

    Reports several realized windows on purpose. The 21-day denominator looks like the
    horizon-matched choice and is NOT: a trailing 21 days can contain an earnings reaction
    while the forward 30 days contains none, so it is cycle-contaminated (measured spread
    0.43-1.61 against 0.73-1.36 for the 252-day window). The shipped line uses the 252-day
    `Price.realized_vol` the repo already computes."""
    import httpx
    iv = {r["ticker"]: r["iv30"] for r in records
          if r.get("status") == 200 and r.get("iv30")}
    rows: list[tuple[str, float, dict]] = []
    headers = {"User-Agent": "Mozilla/5.0 (shortlist options-surface probe)"}
    async with httpx.AsyncClient(timeout=30.0, headers=headers, follow_redirects=True) as c:
        for ticker in sorted(iv):
            try:
                r = await c.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                                params={"range": "2y", "interval": "1d"})
                if r.status_code != 200:
                    continue
                result = r.json()["chart"]["result"][0]
                closes = [x for x in result["indicators"]["quote"][0]["close"] if x is not None]
                vols = {w: _ann_vol(closes, w) for w in (21, 63, 252)}
                if all(vols.values()):
                    rows.append((ticker, iv[ticker], vols))
            except Exception:                            # noqa: BLE001 — a probe reports, never aborts
                continue
            await asyncio.sleep(0.3)
    if not rows:
        print("\nno realized-vol joins available")
        return
    print(f"\n=== IV30 / REALIZED VOL (n={len(rows)}) ===")
    for w in (21, 63, 252):
        ratios = [(iv30 / 100.0) / vols[w] for _, iv30, vols in rows]
        print(f"  realized {w:3}d: {_quantiles(ratios)}")
        print(f"    implied BELOW realized: "
              f"{sum(1 for x in ratios if x < 1.0)} of {len(ratios)}")
    print("  The shipped line uses the 252d window (see this function's docstring).")


def _ann_vol(closes: list[float], window: int) -> Optional[float]:
    rets = [closes[i] / closes[i - 1] - 1.0
            for i in range(1, len(closes)) if closes[i - 1]][-window:]
    if len(rets) < 2:
        return None
    mu = sum(rets) / len(rets)
    var = sum((x - mu) ** 2 for x in rets) / (len(rets) - 1)
    return (var ** 0.5) * (252 ** 0.5)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--universe", choices=("largecap", "smallmid", "both"), default="both")
    ap.add_argument("--limit", type=int, help="first N tickers per universe")
    ap.add_argument("--delay", type=float, default=0.5,
                    help="seconds between requests; below 0.5 invites the 429 (design §4.1)")
    ap.add_argument("--out", type=Path, default=Path("cboe_surface_probe.jsonl"))
    ap.add_argument("--with-realized", action="store_true",
                    help="also fetch Yahoo daily closes and report the IV30/realized-vol "
                         "reference distribution (design §6.1)")
    args = ap.parse_args()

    if args.delay < 0.5:
        ap.error("--delay below 0.5s reproduces the rate-limit lockout, not the evidence")

    picked = ("largecap", "smallmid") if args.universe == "both" else (args.universe,)
    names: list[tuple[str, str]] = []
    for universe in picked:
        tickers = read_universe(universe)
        names += [(universe, t) for t in (tickers[:args.limit] if args.limit else tickers)]

    print(f"probing {len(names)} tickers at {args.delay}s spacing -> {args.out}")
    records = asyncio.run(collect(names, args.delay, args.out))
    summarize(records)
    if args.with_realized:
        asyncio.run(realized_reference(records))


if __name__ == "__main__":
    main()
