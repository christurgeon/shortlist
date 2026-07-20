"""Render a BacktestReport to a dict (JSON), CSV rows, or a rich table.

Also renders the weight-fit report (FitResult) and evaluates the endorsement gate that
decides PROPOSE vs NO-CHANGE for a fitted-weights proposal.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any, Optional

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


def _sign(x: Optional[float]) -> int:
    if x is None:
        return 0
    return 1 if x > 0 else (-1 if x < 0 else 0)


def _fit_caveats() -> list[str]:
    return [
        "Universe is currently-listed names (survivorship bias): IC is an UPPER BOUND; "
        "fitted weights are a PROPOSAL, not tradeable calibration.",
        "The fit speaks only to within-block RATIOS. The fundamental block's total share "
        "(S_f) is an unfitted prior — it is the fundamental axes' current ratio weight, "
        "not a normalized share of the composite. Block share is unanswerable from XBRL.",
        "in_sample_ic is an overfitting diagnostic (coordinate ascent maximizes it); it is "
        "NOT a gate leg.",
        "Fitted ratios are HORIZON-CONDITIONAL (fundamental IC rises with horizon); the "
        "report is for the single stated horizon.",
        "The paired OOS t-stat is anti-conservative: expanding-window folds share training "
        "data, so the per-fold differences are not independent — weight fold-agreement and "
        "the edge margin over the t-stat.",
        "Gross of transaction costs. Point-in-time uses filed <= as_of (re-runs stable), but "
        "a later restatement can shift a past as_of's values — pin the universe + "
        "companyfacts cache month when citing numbers.",
        "standalone_ic is an IN-SAMPLE, cross-sectionally pooled IC over all rows (use for "
        "sign/direction only); it is NOT held-out and is not magnitude-comparable to the "
        "per-period-aggregated OOS ICs above.",
    ]


def fit_report_to_dict(result, *, prior, s_f, horizon, axes, axis_ic, verdict) -> dict:
    prior_sum = sum(prior.values()) or 1.0
    prior_norm = {a: prior[a] / prior_sum for a in axes}
    return {
        "horizon": horizon,
        "axes": {a: {
            "prior_within_block": round(prior_norm[a], 4),
            "fitted_preshrink": round(result.fitted_weights.get(a, 0.0), 4),
            "fitted_shrunk": round(result.weights.get(a, 0.0), 4),
            "config_mapped": round(s_f * result.weights.get(a, 0.0), 4),
            "standalone_ic": (round(axis_ic[a], 4) if axis_ic.get(a) is not None else None),
            "standalone_ic_sign": _sign(axis_ic.get(a)),
        } for a in axes},
        "config_mapped": {a: round(s_f * result.weights.get(a, 0.0), 4) for a in axes},
        "oos": {
            "prior_oos_ic": result.prior_oos_ic,
            "fitted_oos_ic_ceiling": result.oos_ic,
            "shrunk_oos_ic": result.shrunk_oos_ic,
            "in_sample_ic_diagnostic": result.in_sample_ic,
            "n_periods": result.n_periods,
            "n_oos_folds": result.n_oos_folds,
            "fold_diffs": [round(d, 4) for d in result.fold_diffs],
        },
        "verdict": {"endorsed": verdict.endorsed, "reason": verdict.reason},
        "caveats": _fit_caveats(),
    }


def render_fit_report(result, *, prior, s_f, horizon, axes, axis_ic, verdict) -> str:
    import io

    from rich.console import Console
    from rich.table import Table

    buf = io.StringIO()
    con = Console(file=buf, width=120)
    d = fit_report_to_dict(result, prior=prior, s_f=s_f, horizon=horizon, axes=axes,
                           axis_ic=axis_ic, verdict=verdict)
    t = Table(title=f"Weight fit — horizon {horizon}m — "
                    f"{'PROPOSE' if verdict.endorsed else 'NO-CHANGE'}")
    for col in ("axis", "prior", "fitted(pre)", "fitted(shrunk)", "config-mapped", "ic-sign"):
        t.add_column(col)
    for a in axes:
        ax = d["axes"][a]
        t.add_row(a, f"{ax['prior_within_block']:.3f}", f"{ax['fitted_preshrink']:.3f}",
                  f"{ax['fitted_shrunk']:.3f}", f"{ax['config_mapped']:.3f}",
                  {1: "+", -1: "-", 0: "0"}[ax["standalone_ic_sign"]])
    con.print(t)
    o = d["oos"]
    con.print(f"prior OOS IC={o['prior_oos_ic']}  shrunk OOS IC={o['shrunk_oos_ic']}  "
              f"fitted ceiling={o['fitted_oos_ic_ceiling']}  in-sample(diag)="
              f"{o['in_sample_ic_diagnostic']}")
    con.print(f"n_periods={o['n_periods']}  n_oos_folds={o['n_oos_folds']}  "
              f"fold_diffs={o['fold_diffs']}")
    con.print(f"[bold]VERDICT: {'PROPOSE' if verdict.endorsed else 'NO-CHANGE'}[/bold] — "
              f"{verdict.reason}")
    for c in d["caveats"]:
        con.print(f"[dim]• {c}[/dim]")
    return buf.getvalue()
