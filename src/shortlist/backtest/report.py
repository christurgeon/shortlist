"""Render a BacktestReport to a dict (JSON), CSV rows, or a rich table."""
from __future__ import annotations

from typing import Any

from .engine import BacktestReport


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
