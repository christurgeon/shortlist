"""Render a BacktestReport to a dict (JSON), CSV rows, or a rich table.

Also renders the weight-fit report (FitResult) and evaluates the endorsement gate that
decides PROPOSE vs NO-CHANGE for a fitted-weights proposal.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any

from .engine import BacktestReport
from .metrics import aggregate_ic


# Endorsement thresholds (see spec §"Default outcome"). Deliberately hard to clear on
# survivorship-biased data: the default outcome is NO-CHANGE.
GATE_MIN_PERIODS = 36       # well above the engine's describe-floor of 24
GATE_MIN_OOS_FOLDS = 5      # needs n_folds >= 6 (loop is range(1, n_folds))
GATE_MIN_EDGE = 0.02        # mean paired (shrunk - prior) OOS IC; absorbs survivorship inflation
GATE_FOLD_AGREEMENT = 0.8   # >= 4 of 5 folds with a positive paired difference
GATE_MIN_TSTAT = 2.0        # paired-difference t-stat (anti-conservative — see caveats)


@dataclass(frozen=True)
class GateVerdict:
    endorsed: bool
    reason: str


def evaluate_gate(result) -> GateVerdict:
    """Pure endorsement gate over a FitResult. Returns PROPOSE (endorsed=True) only when
    EVERY condition clears, on the per-fold PAIRED (shrunk - prior) difference series —
    not on the fitted level (which would certify the signal, not the fitting). Each single
    failure names the blocking condition."""
    if result.n_periods < GATE_MIN_PERIODS:
        return GateVerdict(False, f"n_periods {result.n_periods} < {GATE_MIN_PERIODS}")
    if result.n_oos_folds < GATE_MIN_OOS_FOLDS:
        return GateVerdict(False, f"n_oos_folds {result.n_oos_folds} < {GATE_MIN_OOS_FOLDS}")
    diffs = result.fold_diffs
    agg = aggregate_ic(diffs)
    mean_d = agg.mean if agg else 0.0
    if mean_d < GATE_MIN_EDGE:
        return GateVerdict(False, f"paired OOS edge {mean_d:.4f} < {GATE_MIN_EDGE}")
    positive = sum(1 for d in diffs if d > 0)
    if positive < ceil(GATE_FOLD_AGREEMENT * len(diffs)):
        return GateVerdict(
            False, f"fold agreement {positive}/{len(diffs)} below {GATE_FOLD_AGREEMENT:.0%}")
    t = agg.t_stat if agg else None
    if t is None or t < GATE_MIN_TSTAT:
        return GateVerdict(False, f"paired t-stat {t} < {GATE_MIN_TSTAT}")
    return GateVerdict(True, "all endorsement conditions cleared (PROPOSE)")


def _ic_dict(ic) -> Any:
    if ic is None:
        return None
    return {"mean": round(ic.mean, 4), "std": round(ic.std, 4),
            "icir": round(ic.icir, 4) if ic.icir is not None else None,
            "t_stat": round(ic.t_stat, 3) if ic.t_stat is not None else None,
            "hit_rate": round(ic.hit_rate, 3), "n": ic.n}


def report_to_dict(r: BacktestReport) -> dict:
    return {
        "universe": r.universe,
        "universe_size": len(r.universe),
        "price_asof": r.price_asof.isoformat() if r.price_asof else None,
        "horizons": r.horizons,
        "return_mode": r.return_mode,
        "caveats": r.caveats,
        "signals": [{
            "signal": s.signal, "horizon": s.horizon,
            "ts_ic": _ic_dict(s.ts_ic), "xs_ic": _ic_dict(s.xs_ic),
            "spread": ({"buckets": [round(b, 4) for b in s.spread.bucket_means],
                        "spread": round(s.spread.spread, 4),
                        "monotonic": s.spread.monotonic,
                        "n_buckets": s.spread.n_buckets}
                       if s.spread else None),
            "n_obs": s.n_obs, "breadth": round(s.breadth, 1), "notes": s.notes,
        } for s in r.reports],
    }


def render_table(r: BacktestReport) -> str:
    import io

    from rich.console import Console
    from rich.table import Table

    buf = io.StringIO()
    con = Console(file=buf, width=120)
    t = Table(title=f"Backtest — {r.return_mode} returns, "
                    f"{len(r.universe)} names, price as of {r.price_asof}")
    for col in ("signal", "h(m)", "TS IC", "TS t", "XS IC", "XS t",
                "hit", "spread", "mono", "n_obs", "breadth"):
        t.add_column(col)
    for s in r.reports:
        ts, xs = s.ts_ic, s.xs_ic
        t.add_row(
            s.signal, str(s.horizon),
            f"{ts.mean:.3f}" if ts else "-",
            f"{ts.t_stat:.2f}" if ts and ts.t_stat is not None else "-",
            f"{xs.mean:.3f}" if xs else "-",
            f"{xs.t_stat:.2f}" if xs and xs.t_stat is not None else "-",
            f"{ts.hit_rate:.2f}" if ts else "-",
            f"{s.spread.spread:.3f}" if s.spread else "-",
            "Y" if s.spread and s.spread.monotonic else "-",
            str(s.n_obs), f"{s.breadth:.1f}")
    con.print(t)
    for c in r.caveats:
        con.print(f"[dim]• {c}[/dim]")
    for s in r.reports:
        for note in s.notes:
            con.print(f"[yellow]! {s.signal} h{s.horizon}: {note}[/yellow]")
    return buf.getvalue()
