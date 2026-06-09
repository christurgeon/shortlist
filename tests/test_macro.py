# tests/test_macro.py
from __future__ import annotations
from shortlist.data.macro import MacroContext, classify_regime

RISK_OFF = {"hy_oas": 5.0, "t10y2y": 0.0, "vix": 25.0}
RISK_ON = {"hy_oas": 3.5, "vix": 18.0}

def test_risk_off_when_hy_oas_blows_out():
    regime, off = classify_regime(
        {"hy_oas": 5.4, "t10y2y": 1.0, "vix": 16.0}, RISK_OFF, RISK_ON)
    assert regime == "risk-off" and off is True

def test_risk_off_when_curve_inverts():
    regime, off = classify_regime(
        {"hy_oas": 3.0, "t10y2y": -0.2, "vix": 16.0}, RISK_OFF, RISK_ON)
    assert regime == "risk-off" and off is True

def test_risk_on_when_calm():
    regime, off = classify_regime(
        {"hy_oas": 2.7, "t10y2y": 0.5, "vix": 15.0}, RISK_OFF, RISK_ON)
    assert regime == "risk-on" and off is False

def test_neutral_between_bands():
    regime, off = classify_regime(
        {"hy_oas": 4.0, "t10y2y": 0.3, "vix": 20.0}, RISK_OFF, RISK_ON)
    assert regime == "neutral" and off is False

def test_none_safe_partial_series():
    # all series missing -> neutral, not risk-off (no condition can trip)
    regime, off = classify_regime(
        {"hy_oas": None, "t10y2y": None, "vix": None}, RISK_OFF, RISK_ON)
    assert regime == "neutral" and off is False

def test_context_holds_fields():
    c = MacroContext(as_of="2026-06-01", dgs10=4.45, t10y2y=0.47,
                     hy_oas=2.72, vix=15.7, fedfunds=3.64,
                     regime="risk-on", risk_off=False)
    assert c.risk_off is False and c.hy_oas == 2.72


import shortlist.data.macro as macro_mod
from shortlist.data.macro import fetch_macro

CFG = {"macro": {
    "enabled": True,
    "series": {"dgs10": "DGS10", "t10y2y": "T10Y2Y", "hy_oas": "BAMLH0A0HYM2",
               "vix": "VIXCLS", "fedfunds": "FEDFUNDS"},
    "risk_off": {"hy_oas": 5.0, "t10y2y": 0.0, "vix": 25.0},
    "risk_on": {"hy_oas": 3.5, "vix": 18.0}}}

_CSV = "observation_date,{id}\n2026-05-30,.\n2026-06-01,{val}\n"

def _fake_series(monkeypatch, values: dict[str, float]):
    calls = {"n": 0}
    def fake_get(series_id: str) -> tuple[str | None, float | None]:
        calls["n"] += 1
        v = values.get(series_id)
        return ("2026-06-01", v)
    monkeypatch.setattr(macro_mod, "_fetch_series", fake_get)
    return calls

def test_fetch_macro_disabled_returns_none(monkeypatch):
    _fake_series(monkeypatch, {})
    assert fetch_macro({"macro": {"enabled": False}}) is None

def test_fetch_macro_builds_context(monkeypatch, tmp_path):
    _fake_series(monkeypatch, {"DGS10": 4.45, "T10Y2Y": -0.2, "BAMLH0A0HYM2": 5.4,
                               "VIXCLS": 16.0, "FEDFUNDS": 3.64})
    monkeypatch.setattr(macro_mod, "_CACHE_DIR", tmp_path)
    c = fetch_macro(CFG)
    assert c is not None and c.regime == "risk-off" and c.hy_oas == 5.4

def test_fetch_macro_day_cache_avoids_second_pull(monkeypatch, tmp_path):
    calls = _fake_series(monkeypatch, {"DGS10": 4.45})
    monkeypatch.setattr(macro_mod, "_CACHE_DIR", tmp_path)
    fetch_macro(CFG); first = calls["n"]
    fetch_macro(CFG)
    assert calls["n"] == first  # second run served from disk cache

def test_fetch_macro_never_raises(monkeypatch, tmp_path):
    def boom(series_id): raise RuntimeError("network down ?apikey=SECRET")
    monkeypatch.setattr(macro_mod, "_fetch_series", boom)
    monkeypatch.setattr(macro_mod, "_CACHE_DIR", tmp_path)
    assert fetch_macro(CFG) is None  # degrades, no exception
