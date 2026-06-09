# src/shortlist/data/macro.py
"""Run-level FRED macro/credit-regime overlay. Keyless, day-cached, never-raises.

NOT a per-ticker Source: built once per run and threaded into score() (one soft
advisory flag) and the report header. Display + advisory only — never touches
composite/gates/ranking.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MacroContext:
    as_of: str | None
    dgs10: float | None
    t10y2y: float | None
    hy_oas: float | None
    vix: float | None
    fedfunds: float | None
    regime: str
    risk_off: bool


def classify_regime(vals: dict[str, float | None],
                    risk_off_cfg: dict, risk_on_cfg: dict) -> tuple[str, bool]:
    """Derive ("risk-on"|"neutral"|"risk-off", risk_off_bool). None-safe: a
    missing series simply cannot trip its condition."""
    hy, curve, vix = vals.get("hy_oas"), vals.get("t10y2y"), vals.get("vix")

    off = ((hy is not None and hy > risk_off_cfg["hy_oas"])
           or (curve is not None and curve < risk_off_cfg["t10y2y"])
           or (vix is not None and vix > risk_off_cfg["vix"]))
    if off:
        return "risk-off", True

    calm = ((hy is not None and hy < risk_on_cfg["hy_oas"])
            or (vix is not None and vix < risk_on_cfg["vix"]))
    return ("risk-on", False) if calm else ("neutral", False)
