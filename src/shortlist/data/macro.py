# src/shortlist/data/macro.py
"""Run-level FRED macro/credit-regime overlay. Official FRED API (needs a free
FRED_API_KEY), day-cached, never-raises.

NOT a per-ticker Source: built once per run and threaded into score() (one soft
advisory flag) and the report header. Display + advisory only — never touches
composite/gates/ranking.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..env import redact_secrets

_CACHE_DIR = Path(".cache/fred")
_FRED_API = "https://api.stlouisfed.org/fred/series/observations"
_TIMEOUT = 8.0


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


def _fetch_series(series_id: str, api_key: str) -> tuple[str | None, float | None]:
    """Latest non-missing (date, value) for a FRED series via the official API.
    FRED encodes a missing observation as '.'; we pull the most recent few and
    take the latest real value."""
    import httpx  # lazy: only needed for live runs
    r = httpx.get(_FRED_API, params={
        "series_id": series_id, "api_key": api_key, "file_type": "json",
        "sort_order": "desc", "limit": 10}, timeout=_TIMEOUT)
    r.raise_for_status()
    for obs in r.json().get("observations", []):
        raw = (obs.get("value") or "").strip()
        if raw and raw != ".":
            return obs.get("date"), float(raw)
    return None, None


def fetch_macro(config: dict) -> MacroContext | None:
    """Run-level macro overlay via the official FRED API (needs a free
    FRED_API_KEY). Returns None when disabled, unkeyed, or on total failure
    (→ byte-identical downstream). Day-cached under .cache/fred/."""
    cfg = (config or {}).get("macro") or {}
    if not cfg.get("enabled"):
        return None
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        print("[macro] FRED_API_KEY not set; macro overlay disabled", file=sys.stderr)
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
                d, v = _fetch_series(sid, api_key)
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
        print(f"[macro] overlay unavailable: {redact_secrets(str(e))}", file=sys.stderr)
        return None
