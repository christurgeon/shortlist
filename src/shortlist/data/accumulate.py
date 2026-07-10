"""Daily point-in-time snapshot accumulation.

Captures one `TickerSnapshot` per ticker per UTC day into the store so the backtest
snapshot-replay path accumulates real history. Idempotent (skips already-captured
days before spending any API call), per-ticker isolated (one bad name can't abort a
run), and observable (per-run log + a status verdict against the backtest's
24-date **and** 30-name-breadth thresholds).

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
from dataclasses import fields as _dc_fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from ..env import load_env, redact_secrets
from .collector import collect
from .store import captured_days, load, save

DEFAULT_MAX_TICKERS = 15          # ~13 FMP calls/ticker -> <= ~195/day < 250 free cap
MIN_SNAPSHOT_DATES = 24           # mirrors the backtest snapshot/fit guard
THIN_MARK = 0.5                   # classification only: saved-but-thin (< 50% key-field coverage)
MIN_SNAPSHOT_BREADTH = 30         # mirrors backtest/engine.py _TRUST_MIN_BREADTH


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def is_captured(ticker: str, root: str | Path, day: str) -> bool:
    return day in captured_days(ticker, root)


def load_watchlist(spec: str) -> list[str]:
    """'default' -> bundled watchlist file; otherwise a comma-separated ticker list."""
    if spec == "default":
        path = Path(__file__).parent / "accumulate_watchlist.txt"
        return [ln.strip().upper() for ln in path.read_text().splitlines()
                if ln.strip() and not ln.strip().startswith("#")]
    return [t.strip().upper() for t in spec.split(",") if t.strip()]


@dataclass
class AccumulationRun:
    day: str
    attempted: int
    captured: list[tuple[str, float]] = field(default_factory=list)   # saved, coverage >= THIN_MARK
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)       # (ticker, redacted err)
    thin: list[tuple[str, float]] = field(default_factory=list)       # SAVED, coverage < THIN_MARK
    gated: list[tuple[str, float]] = field(default_factory=list)      # NOT saved (< min_coverage)

    @property
    def mean_coverage(self) -> Optional[float]:
        covs = [c for _, c in self.captured + self.thin]
        return round(sum(covs) / len(covs), 3) if covs else None


_BOOKKEEPING = ("ticker", "as_of", "raw", "provenance", "errors")


def _is_empty(snap) -> bool:
    """Nothing fetched at all: zero key-field coverage AND every section object
    (key + aux) absent. A Finnhub-earnings-only snapshot has coverage()==0.0
    (aux is excluded from coverage) but is NOT empty — it is the SUE payload."""
    if snap.coverage() > 0.0:
        return False
    return all(getattr(snap, f.name) is None
               for f in _dc_fields(type(snap)) if f.name not in _BOOKKEEPING)


def accumulate(tickers: list[str], sources: list[str], root: str | Path, *,
               force: bool = False, max_tickers: Optional[int] = DEFAULT_MAX_TICKERS,
               min_coverage: float = 0.0,
               collect_fn: Optional[Callable] = None) -> AccumulationRun:
    """Capture today's snapshot for each ticker. Idempotent and per-ticker isolated.

    min_coverage: an explicit save-gate — snapshots below this coverage fraction
    are GATED (not saved, not counted toward readiness) so a caller can still
    exclude fully/partly gated symbols (e.g. FMP's per-symbol 402) on demand. The
    default is 0.0 (gate off): everything actually fetched is saved. A snapshot
    that clears the gate but still has < THIN_MARK (50%) key-field coverage is
    SAVED and classified THIN, not dropped — it still carries whatever
    keyless/aux sections (price, earnings) landed, which is exactly what the
    replay/SUE axes need. A snapshot with genuinely nothing fetched from any
    source (total outage) FAILS instead of being saved as an empty husk.
    collect_fn is injectable for testing; defaults to the real collector.
    """
    cf = collect_fn or collect                        # resolved at call time (testable via monkeypatch)
    day = _today_iso()
    # `None` = no cap; an explicit 0 means "capture nothing" (not "all").
    sel = list(tickers) if max_tickers is None else tickers[:max_tickers]
    run = AccumulationRun(day=day, attempted=len(sel))
    for raw in sel:
        tk = raw.upper()
        if not force and is_captured(tk, root, day):
            run.skipped.append(tk)
            continue                                  # skip BEFORE any API call
        try:
            snaps = cf([tk], sources)
            if not snaps:
                run.failed.append((tk, "no snapshot returned"))
                continue
            snap = snaps[0]
            if snap.as_of[:10] < day:                 # integrity: never accept a past day
                run.failed.append((tk, f"stale as_of {snap.as_of[:10]} < {day}"))
                continue
            cov = snap.coverage()
            if cov < min_coverage:                    # explicit gate (CLI default 0.0 = off)
                run.gated.append((tk, cov))
                continue
            if _is_empty(snap):                       # total outage: nothing to store
                run.failed.append((tk, "no data from any source"))
                continue
            save(snap, root)
            (run.captured if cov >= THIN_MARK else run.thin).append((tk, cov))
        except Exception as e:                        # one bad ticker can't abort the run
            run.failed.append((tk, redact_secrets(e)))
    _append_run_log(root, run)
    return run


def _append_run_log(root: str | Path, run: AccumulationRun) -> None:
    # Append-only; intentionally NOT rotated (one line/run ~= 365 lines/year).
    Path(root).mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "day": run.day, "attempted": run.attempted,
        "captured": len(run.captured), "skipped": len(run.skipped),
        "failed": len(run.failed), "thin": len(run.thin), "gated": len(run.gated),
        "mean_coverage": run.mean_coverage,
        "coverage": dict(run.captured + run.thin),
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
    per_date: dict[str, int] = field(default_factory=dict)            # saved names/date
    per_date_earnings: dict[str, int] = field(default_factory=dict)   # SUE-bearing names/date
    min_breadth: int = MIN_SNAPSHOT_BREADTH
    breadth_dates: int = 0            # dates meeting min_breadth
    breadth_met: bool = False
    store_bytes: int = 0


def store_status(root: str | Path, tickers: list[str], *,
                 min_dates: int = MIN_SNAPSHOT_DATES) -> StatusReport:
    """How close the store is to BOTH backtest snapshot-replay floors:
    >=min_dates distinct dates AND >=MIN_SNAPSHOT_BREADTH names per date."""
    per: dict[str, int] = {}
    per_date: dict[str, int] = {}
    per_date_earn: dict[str, int] = {}
    size = 0
    for tk in tickers:
        t = tk.upper()
        days = captured_days(t, root)
        per[t] = len(days)
        for d in days:
            per_date[d] = per_date.get(d, 0) + 1
            try:
                raw = load(t, root, day=d)
                if raw.get("earnings"):
                    per_date_earn[d] = per_date_earn.get(d, 0) + 1
            except (FileNotFoundError, OSError, ValueError):
                pass
        tdir = Path(root) / t
        if tdir.is_dir():
            size += sum(p.stat().st_size for p in tdir.iterdir() if p.is_file())
    distinct = sorted(per_date)
    breadth_dates = sum(1 for d in distinct if per_date[d] >= MIN_SNAPSHOT_BREADTH)
    return StatusReport(
        n_dates=len(distinct), distinct_dates=distinct, per_ticker=per,
        min_dates=min_dates, threshold_met=len(distinct) >= min_dates,
        per_date=per_date, per_date_earnings=per_date_earn,
        min_breadth=MIN_SNAPSHOT_BREADTH, breadth_dates=breadth_dates,
        breadth_met=breadth_dates >= min_dates, store_bytes=size)


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
    run.add_argument("--min-coverage", dest="min_coverage", type=float, default=0.0,
                     help="explicit save-gate: snapshots below this coverage fraction are "
                          "NOT saved (default 0.0 = save everything fetched; thin snapshots "
                          "carry the keyless earnings/price sections the replay axes need)")
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
        run = accumulate(tickers, sources, args.root, force=args.force,
                         max_tickers=args.max_tickers, min_coverage=args.min_coverage)
        for tk, cov in run.captured:
            print(f"{tk:<6} captured  coverage={cov:>5.0%}")
        for tk, cov in run.thin:
            print(f"{tk:<6} thin      coverage={cov:>5.0%} (saved; FMP-gated or partial)")
        for tk, cov in run.gated:
            print(f"{tk:<6} GATED     coverage={cov:>5.0%} (< {args.min_coverage:.0%}, not saved)")
        for tk in run.skipped:
            print(f"{tk:<6} skipped   (already captured {run.day})")
        for tk, err in run.failed:
            print(f"{tk:<6} FAILED    {err}")
        mc = f"{run.mean_coverage:.0%}" if run.mean_coverage is not None else "-"
        print(f"\n{run.day}: captured={len(run.captured)} thin={len(run.thin)} "
              f"gated={len(run.gated)} skipped={len(run.skipped)} failed={len(run.failed)} "
              f"mean_coverage={mc}")
        print(_DISABLED_BANNER, file=sys.stderr)
        return 0 if not run.failed else 1

    # status
    rep = store_status(args.root, tickers, min_dates=args.min_dates)
    print(f"store: {args.root}")
    ready = rep.threshold_met and rep.breadth_met
    print(f"distinct capture dates: {rep.n_dates} / {rep.min_dates} needed")
    print(f"dates with breadth >= {rep.min_breadth}: {rep.breadth_dates} / {rep.min_dates} needed")
    if not rep.breadth_met and rep.per_date:
        worst = min(rep.per_date.values())
        best = max(rep.per_date.values())
        print(f"NOT READY: breadth < {rep.min_breadth} on "
              f"{len(rep.per_date) - rep.breadth_dates}/{len(rep.per_date)} dates "
              f"(per-date saved names range {worst}-{best})")
    print(f"earnings-bearing (SUE) breadth today: "
          f"{rep.per_date_earnings.get(rep.distinct_dates[-1], 0) if rep.distinct_dates else 0}")
    print(f"store size: {rep.store_bytes / 1e6:.1f} MB")
    print(f"-> snapshot backtest {'READY' if ready else 'NOT READY'}")
    for tk, n in sorted(rep.per_ticker.items()):
        print(f"  {tk:<6} {n} day(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
