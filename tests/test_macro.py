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
