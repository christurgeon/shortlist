"""Daily point-in-time snapshot accumulation.

Captures one `TickerSnapshot` per ticker per UTC day into the store so the backtest
snapshot-replay path accumulates real history. Idempotent (skips already-captured
days before spending any API call), per-ticker isolated (one bad name can't abort a
run), and observable (per-run log + a status verdict against the backtest's
24-date threshold).

Point-in-time integrity: capture is ALWAYS the current UTC day. There is no path to
write a snapshot dated to a past day — `as_of` comes from `utcnow` via collect, and
a snapshot older than the run day is rejected. Backfilled/restated data would
silently reintroduce look-ahead into the backtest, so it is forbidden by design.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from ..env import load_env, redact_secrets
from .collector import collect
from .store import captured_days, save

DEFAULT_MAX_TICKERS = 15          # ~13 FMP calls/ticker -> <= ~195/day < 250 free cap
MIN_SNAPSHOT_DATES = 24           # mirrors the backtest snapshot/fit guard


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def is_captured(ticker: str, root: str | Path, day: str) -> bool:
    return day in captured_days(ticker, root)


def load_watchlist(spec: str) -> list[str]:
    """'default' -> bundled watchlist file; otherwise a comma-separated ticker list."""
    if spec == "default":
        path = Path(__file__).parent / "accumulate_watchlist.txt"
        return [ln.strip().upper() for ln in path.read_text().splitlines()
                if ln.strip() and not ln.startswith("#")]
    return [t.strip().upper() for t in spec.split(",") if t.strip()]


@dataclass
class AccumulationRun:
    day: str
    attempted: int
    captured: list[tuple[str, float]] = field(default_factory=list)   # (ticker, coverage)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)       # (ticker, redacted err)

    @property
    def mean_coverage(self) -> Optional[float]:
        covs = [c for _, c in self.captured]
        return round(sum(covs) / len(covs), 3) if covs else None


def accumulate(tickers: list[str], sources: list[str], root: str | Path, *,
               force: bool = False, max_tickers: Optional[int] = DEFAULT_MAX_TICKERS,
               collect_fn: Callable = collect) -> AccumulationRun:
    """Capture today's snapshot for each ticker. Idempotent and per-ticker isolated.

    collect_fn is injectable for testing; defaults to the real collector.
    """
    day = _today_iso()
    sel = tickers[:max_tickers] if max_tickers else list(tickers)
    run = AccumulationRun(day=day, attempted=len(sel))
    for raw in sel:
        tk = raw.upper()
        if not force and is_captured(tk, root, day):
            run.skipped.append(tk)
            continue                                  # skip BEFORE any API call
        try:
            snaps = collect_fn([tk], sources)
            if not snaps:
                run.failed.append((tk, "no snapshot returned"))
                continue
            snap = snaps[0]
            if snap.as_of[:10] < day:                 # integrity: never accept a past day
                run.failed.append((tk, f"stale as_of {snap.as_of[:10]} < {day}"))
                continue
            save(snap, root)
            run.captured.append((tk, snap.coverage()))
        except Exception as e:                        # one bad ticker can't abort the run
            run.failed.append((tk, redact_secrets(e)))
    _append_run_log(root, run)
    return run


def _append_run_log(root: str | Path, run: AccumulationRun) -> None:
    Path(root).mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "day": run.day, "attempted": run.attempted,
        "captured": len(run.captured), "skipped": len(run.skipped),
        "failed": len(run.failed), "mean_coverage": run.mean_coverage,
    }
    with open(Path(root) / "_runs.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")


@dataclass
class StatusReport:
    n_dates: int
    distinct_dates: list[str]
    per_ticker: dict[str, int]
    min_dates: int
    threshold_met: bool


def store_status(root: str | Path, tickers: list[str], *,
                 min_dates: int = MIN_SNAPSHOT_DATES) -> StatusReport:
    """How close the store is to the backtest's snapshot-replay threshold."""
    per: dict[str, int] = {}
    all_dates: set[str] = set()
    for tk in tickers:
        days = captured_days(tk, root)
        per[tk.upper()] = len(days)
        all_dates.update(days)
    distinct = sorted(all_dates)
    return StatusReport(n_dates=len(distinct), distinct_dates=distinct,
                        per_ticker=per, min_dates=min_dates,
                        threshold_met=len(distinct) >= min_dates)


# --- CLI ------------------------------------------------------------------

_DISABLED_BANNER = (
    "NOTE: scheduling is NOT enabled. This captures one day when you run it. "
    "See deploy/README.md to enable a daily timer (off by default)."
)


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="shortlist-accumulate",
        description="Capture point-in-time snapshots for the backtest. " + _DISABLED_BANNER)
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="capture today's snapshots (idempotent)")
    run.add_argument("--watchlist", default="default",
                     help="'default' (bundled) or comma-separated tickers")
    run.add_argument("--tickers", help="comma-separated tickers (overrides --watchlist)")
    run.add_argument("--sources", default="fmp,finnhub")
    run.add_argument("--root", default="snapshots", help="store root directory")
    run.add_argument("--max-tickers", dest="max_tickers", type=int,
                     default=DEFAULT_MAX_TICKERS)
    run.add_argument("--force", action="store_true",
                     help="re-capture even if today's snapshot already exists")

    st = sub.add_parser("status", help="progress toward the backtest 24-date threshold")
    st.add_argument("--watchlist", default="default")
    st.add_argument("--tickers")
    st.add_argument("--root", default="snapshots")
    st.add_argument("--min-dates", dest="min_dates", type=int, default=MIN_SNAPSHOT_DATES)
    return ap


def main(argv=None) -> int:
    load_env()
    args = build_arg_parser().parse_args(argv)
    tickers = load_watchlist(args.tickers or args.watchlist)

    if args.cmd == "run":
        sources = [s.strip() for s in args.sources.split(",") if s.strip()]
        run = accumulate(tickers, sources, args.root,
                         force=args.force, max_tickers=args.max_tickers)
        for tk, cov in run.captured:
            print(f"{tk:<6} captured  coverage={cov:>5.0%}")
        for tk in run.skipped:
            print(f"{tk:<6} skipped   (already captured {run.day})")
        for tk, err in run.failed:
            print(f"{tk:<6} FAILED    {err}")
        mc = f"{run.mean_coverage:.0%}" if run.mean_coverage is not None else "-"
        print(f"\n{run.day}: captured={len(run.captured)} skipped={len(run.skipped)} "
              f"failed={len(run.failed)} mean_coverage={mc}")
        print(_DISABLED_BANNER, file=sys.stderr)
        return 0 if not run.failed else 1

    # status
    rep = store_status(args.root, tickers, min_dates=args.min_dates)
    print(f"store: {args.root}")
    print(f"distinct capture dates: {rep.n_dates} / {rep.min_dates} needed "
          f"-> snapshot backtest {'READY' if rep.threshold_met else 'NOT READY'}")
    for tk, n in sorted(rep.per_ticker.items()):
        print(f"  {tk:<6} {n} day(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
