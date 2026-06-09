# src/shortlist/data/macro.py
"""Run-level FRED macro/credit-regime overlay. Keyless, day-cached, never-raises.

NOT a per-ticker Source: built once per run and threaded into score() (one soft
advisory flag) and the report header. Display + advisory only — never touches
composite/gates/ranking.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx

from ..env import redact_secrets

_CACHE_DIR = Path(".cache/fred")
_FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}"
_TIMEOUT = 15.0


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


def _fetch_series(series_id: str) -> tuple[str | None, float | None]:
    """Last non-missing (date, value) for a FRED series, or (None, None).
    FRED writes '.' for missing observations — skip those."""
    r = httpx.get(_FRED_CSV.format(id=series_id), timeout=_TIMEOUT)
    r.raise_for_status()
    last_date = last_val = None
    for line in r.text.splitlines()[1:]:          # skip header
        parts = line.split(",")
        if len(parts) != 2:
            continue
        d, raw = parts[0].strip(), parts[1].strip()
        if raw and raw != ".":
            last_date, last_val = d, float(raw)
    return last_date, last_val


def fetch_macro(config: dict) -> MacroContext | None:
    """Run-level macro overlay. Returns None when disabled or on total failure
    (→ byte-identical downstream). Day-cached under .cache/fred/."""
    cfg = (config or {}).get("macro") or {}
    if not cfg.get("enabled"):
        return None
    try:
        series: dict[str, str] = cfg["series"]
        cache = _CACHE_DIR / f"{date.today().isoformat()}.json"
        if cache.exists():
            raw = json.loads(cache.read_text())
        else:
            raw = {}
            as_of = None
            for key, sid in series.items():
                d, v = _fetch_series(sid)
                raw[key] = v
                as_of = as_of or d
            raw["as_of"] = as_of
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(raw))

        vals = {k: raw.get(k) for k in series}
        regime, off = classify_regime(vals, cfg["risk_off"], cfg["risk_on"])
        return MacroContext(
            as_of=raw.get("as_of"),
            dgs10=vals.get("dgs10"), t10y2y=vals.get("t10y2y"),
            hy_oas=vals.get("hy_oas"), vix=vals.get("vix"),
            fedfunds=vals.get("fedfunds"),
            regime=regime, risk_off=off)
    except Exception as e:               # never sink a run on the macro overlay
        print(f"[macro] overlay unavailable: {redact_secrets(str(e))}")
        return None
