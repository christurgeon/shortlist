"""``shortlist-harness`` CLI: fetch and emit raw ``TickerSnapshot``s per ticker."""
from __future__ import annotations

import argparse
import json
import sys

from ..env import load_env
from .collector import collect
from .store import save


def main(argv: list[str] | None = None) -> int:
    """Fetch snapshots for the given tickers, print a per-ticker coverage line,
    optionally save dated JSON (``--out``) and dump full JSON (``--print``)."""
    ap = argparse.ArgumentParser(prog="shortlist-harness",
                                 description="Fetch an assessment-ready data snapshot per ticker.")
    ap.add_argument("--tickers", required=True, help="comma-separated, e.g. GEV,LMT,SCHW")
    ap.add_argument("--sources", default="fmp,finnhub",
                    help="comma-separated source chain (default: fmp,finnhub; use 'mock' offline)")
    ap.add_argument("--out", help="directory to write dated JSON snapshots")
    ap.add_argument("--print", dest="show", action="store_true", help="print full JSON to stdout")
    ap.add_argument("--no-cache", action="store_true",
                    help="disable the on-disk HTTP cache for this run")
    ap.add_argument("--refresh-cache", action="store_true",
                    help="bypass cached HTTP responses and repopulate them")
    args = ap.parse_args(argv)

    load_env()  # pick up API keys from a .env file if present

    # This CLI loads no config.yaml, so the cache uses hardcoded TTL defaults (the
    # spec's on-by-default behavior holds without a config block).
    from ..cache import configure_default_cache
    configure_default_cache(enabled=not args.no_cache, refresh=args.refresh_cache)

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    snapshots = collect(tickers, sources)
    if not snapshots:
        print("No data collected (no usable sources?).", file=sys.stderr)
        return 1

    for snap in snapshots:
        if args.out:
            path = save(snap, args.out)
            saved = f" -> {path}"
        else:
            saved = ""
        cov = snap.coverage()
        ok = cov >= 0.8
        flag = "ok" if ok else "thin"
        miss = "" if ok else f"  missing: {', '.join(snap.missing()[:6])}"
        srcs = ",".join(sorted({s for v in snap.provenance.values() for s in v})) or "-"
        print(f"{snap.ticker:<6} coverage={cov:>5.0%} [{flag}] sources={srcs}{saved}{miss}")
        if snap.errors:
            print(f"        errors: {'; '.join(snap.errors[:4])}")

    if args.show:
        print(json.dumps([s.to_dict() for s in snapshots], indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
