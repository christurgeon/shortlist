"""shortlist-backtest — validate scores against forward returns (rank IC + spreads)."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import yaml

from ..env import load_env
from .engine import run_backtest
from .prices import _UA, _add_months, fetch_history
from .report import render_table, report_to_dict
from .signals import MomentumSignalSource, XbrlSignalSource
from .universe import load_universe
from .xbrl import fetch_cik_index, fetch_companyfacts, cik_for


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="shortlist-backtest",
        description="Validate screener scores against forward returns.")
    ap.add_argument("--universe", default="largecap",
                    help="'largecap' (bundled) or comma-separated tickers")
    ap.add_argument("--tickers", help="alias for an ad-hoc --universe CSV")
    ap.add_argument("--horizons", default="3",
                    help="comma-separated months, e.g. 1,3,6,12")
    ap.add_argument("--buckets", type=int, default=5)
    ap.add_argument("--return-mode", dest="return_mode",
                    choices=["excess", "raw"], default="excess")
    ap.add_argument("--source", choices=["momentum", "snapshot", "xbrl"],
                    default="momentum")
    ap.add_argument("--xbrl-cache-dir", dest="xbrl_cache_dir",
                    default=".cache/sec_xbrl",
                    help="disk cache for companyfacts + the SEC ticker map")
    ap.add_argument("--step-months", dest="step_months", type=int, default=0,
                    help="grid spacing; 0 = non-overlapping (= max horizon)")
    ap.add_argument("--start", help="grid start YYYY-MM-DD (default ~ earliest usable)")
    ap.add_argument("--end", help="grid end YYYY-MM-DD (default today)")
    ap.add_argument("--config", default=str(Path(__file__).parents[3] / "config.yaml"))
    ap.add_argument("--cache-dir", dest="cache_dir", default=".cache/yahoo")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--csv", help="write per-signal rows to this CSV path")
    return ap


async def _load_companyfacts(tickers, cache_dir, month):
    """Resolve CIKs and fetch companyfacts for the universe. Keyless; SEC
    fair-access requires a descriptive User-Agent carrying a contact email.
    IFRS 20-F foreign issuers have no us-gaap facts and degrade to a skip."""
    identity = os.environ.get("SEC_IDENTITY")
    if not identity:
        raise RuntimeError(
            "SEC_IDENTITY (a contact email) is required by the SEC for the XBRL "
            "source — set it in .env, e.g. SEC_IDENTITY='you@example.com'.")
    facts = {}
    async with httpx.AsyncClient(headers={"User-Agent": identity},
                                 timeout=30.0) as client:
        index = await fetch_cik_index(client, cache_dir=cache_dir, month=month)
        for tk in tickers:
            cik = cik_for(tk, index)
            if cik is None:
                print(f"warn: {tk} not in SEC ticker map", file=sys.stderr)
                continue
            try:
                cf = await fetch_companyfacts(cik, client, cache_dir=cache_dir,
                                              month=month)
                if cf is not None:
                    facts[tk] = cf
                else:
                    print(f"warn: {tk} has no us-gaap companyfacts "
                          f"(IFRS/20-F foreign issuer or recent spin-off)",
                          file=sys.stderr)
            except Exception as e:
                print(f"warn: {tk} companyfacts fetch failed: {type(e).__name__}",
                      file=sys.stderr)
    return facts


async def _load_histories(tickers, cache_dir, today):
    async with httpx.AsyncClient(headers={"User-Agent": _UA}) as client:
        spy = await fetch_history("SPY", client, cache_dir=cache_dir, today=today)
        hists = {}
        for tk in tickers:
            try:
                hists[tk] = await fetch_history(tk, client, cache_dir=cache_dir,
                                                today=today)
            except Exception as e:               # keep going; report coverage
                print(f"warn: {tk} price fetch failed: {type(e).__name__}",
                      file=sys.stderr)
    return hists, spy


def _grid_start(earliest: date) -> date:
    # need >= 200 trading days before the first signal; ~14 calendar months
    # comfortably clears 200 trading days (11 months ~= 160, too few).
    return _add_months(earliest, 14)


def _write_csv(report, path):
    import csv

    d = report_to_dict(report)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["signal", "horizon", "ts_ic_mean", "ts_t", "xs_ic_mean",
                    "xs_t", "hit_rate", "spread", "n_obs", "breadth"])
        for s in d["signals"]:
            ts, xs = s["ts_ic"], s["xs_ic"]
            w.writerow([s["signal"], s["horizon"],
                        ts["mean"] if ts else "", ts["t_stat"] if ts else "",
                        xs["mean"] if xs else "", xs["t_stat"] if xs else "",
                        ts["hit_rate"] if ts else "",
                        s["spread"]["spread"] if s["spread"] else "",
                        s["n_obs"], s["breadth"]])


def main(argv=None) -> int:
    load_env()  # pick up SEC_IDENTITY / API keys from a .env file if present
    args = build_arg_parser().parse_args(argv)
    if args.source == "snapshot":
        print("snapshot source is GATED: no organic point-in-time snapshot history "
              "exists yet (needs >= 24 daily captures). Use --source momentum.",
              file=sys.stderr)
        return 2

    if args.source == "xbrl" and not os.environ.get("SEC_IDENTITY"):
        print("SEC_IDENTITY (a contact email) is required for --source xbrl — "
              "set it in .env, e.g. SEC_IDENTITY='you@example.com'.", file=sys.stderr)
        return 2

    tickers = load_universe(args.tickers or args.universe)
    horizons = [int(h) for h in args.horizons.split(",")]
    if any(h < 1 for h in horizons):
        print("--horizons must be positive integer months (>= 1)", file=sys.stderr)
        return 2
    today = datetime.now(tz=timezone.utc).date().isoformat()
    config = yaml.safe_load(Path(args.config).read_text())
    thresholds = config["thresholds"]

    hists, spy = asyncio.run(_load_histories(tickers, args.cache_dir, today))
    hists = {k: v for k, v in hists.items() if v.dates}
    if not hists or not spy.dates:
        print("no price history available", file=sys.stderr)
        return 1

    earliest = min(min(h.dates) for h in hists.values())
    start = date.fromisoformat(args.start) if args.start else _grid_start(earliest)
    end = date.fromisoformat(args.end) if args.end else date.fromisoformat(today)

    if args.source == "xbrl":
        month = today[:7]   # YYYY-MM — companyfacts is month-cached
        facts = asyncio.run(_load_companyfacts(tickers, args.xbrl_cache_dir, month))
        if not facts:
            print("no companyfacts available for the universe", file=sys.stderr)
            return 1
        src = XbrlSignalSource(facts, hists, thresholds)
    else:
        src = MomentumSignalSource(hists, spy, thresholds, min_history=200)
    report = run_backtest([src], hists, spy, start=start, end=end,
                          horizons=horizons, step_months=(args.step_months or None),
                          n_buckets=args.buckets, return_mode=args.return_mode,
                          price_asof=date.fromisoformat(today))

    if args.csv:
        _write_csv(report, args.csv)
    if args.json:
        print(json.dumps(report_to_dict(report), indent=2))
    else:
        print(render_table(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
