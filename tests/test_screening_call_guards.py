from types import SimpleNamespace
from shortlist.models import Coverage
from shortlist.research.models import ScreeningCall, QualitativeAssessment, Conflict
from shortlist.research.assess import apply_guards

CFG = {"research": {"screening_call": {
    "gate_clamp": {"_default": "HOLD", "negative_fcf": "AVOID", "over_leveraged": "AVOID"},
    "conviction_cap": {"low_below": 0.45, "medium_below": 0.70},
    "high_conviction": {"contra_flags": ["value_trap"]},
}}}


def _card(**kw):
    base = dict(gates=[], flags=[], confidence=1.0, coverage=None, abstentions=[],
                sic_bucket="unknown", metrics=SimpleNamespace(price=123.45))
    base.update(kw)
    return SimpleNamespace(**base)


def _assess(call, **recon_kw):
    a = QualitativeAssessment(ticker="X", as_of="", filing_accession="", filing_date="",
                              model="")
    a.screening_call = call
    a.reconciliation = recon_kw.get("recon", [])
    a.red_flags = recon_kw.get("red_flags", [])
    return a


def _confirm():
    c = Conflict(signal="growth", tension="t", filing_says="quote here long enough",
                 verdict="confirms")
    c.verified = True
    return c


def test_gate_clamp_moves_bearish_and_caps_conviction():
    a = _assess(ScreeningCall(stance="STRONG_BUY", conviction="HIGH", rationale="bull"))
    apply_guards(a, _card(gates=["negative_fcf"]), CFG)
    assert a.screening_call.stance == "AVOID"      # clamped no-more-bullish-than AVOID
    assert a.screening_call.stance_clamped is True
    assert a.screening_call.conviction == "MEDIUM"  # clamp caps conviction
    assert a.screening_call.conviction_capped is True
    assert "negative_fcf" in a.screening_call.clamp_note


def test_clamp_never_upgrades():
    a = _assess(ScreeningCall(stance="STRONG_AVOID", conviction="LOW"))
    apply_guards(a, _card(gates=["below_min_mktcap"]), CFG)  # ceiling HOLD
    assert a.screening_call.stance == "STRONG_AVOID"  # already more bearish than HOLD
    assert a.screening_call.stance_clamped is False


def test_most_bearish_ceiling_wins():
    a = _assess(ScreeningCall(stance="STRONG_BUY", conviction="LOW"))
    apply_guards(a, _card(gates=["below_min_mktcap", "negative_fcf"]), CFG)
    assert a.screening_call.stance == "AVOID"


def test_conviction_cap_thin_confidence():
    a = _assess(ScreeningCall(stance="BUY", conviction="HIGH", rationale="r"), recon=[_confirm()])
    apply_guards(a, _card(confidence=0.40), CFG)
    assert a.screening_call.conviction == "LOW"


def test_confidence_none_forces_low():
    a = _assess(ScreeningCall(stance="BUY", conviction="HIGH"))
    apply_guards(a, _card(confidence=None), CFG)
    assert a.screening_call.conviction == "LOW"


def test_decided_without_caps_to_medium():
    cov = Coverage(providers={"fmp": "gated_402"}, unavailable=["value"], note="x")
    a = _assess(ScreeningCall(stance="BUY", conviction="HIGH", rationale="r"),
                recon=[_confirm()])
    apply_guards(a, _card(value=None, confidence=0.95, coverage=cov), CFG)
    assert a.screening_call.conviction == "MEDIUM"
    assert a.screening_call.decided_without


def test_high_demoted_without_corroboration():
    a = _assess(ScreeningCall(stance="BUY", conviction="HIGH", rationale="r"), recon=[])
    apply_guards(a, _card(confidence=0.95), CFG)
    assert a.screening_call.conviction == "MEDIUM"  # no confirming reconciliation


def test_high_survives_with_corroboration():
    a = _assess(ScreeningCall(stance="BUY", conviction="HIGH", rationale="r"),
                recon=[_confirm()])
    apply_guards(a, _card(confidence=0.95), CFG)
    assert a.screening_call.conviction == "HIGH"


def test_high_blocked_by_contra_flag():
    a = _assess(ScreeningCall(stance="BUY", conviction="HIGH", rationale="r"),
                recon=[_confirm()])
    apply_guards(a, _card(confidence=0.95, flags=["value_trap"]), CFG)
    assert a.screening_call.conviction == "MEDIUM"


def test_as_of_price_captured():
    a = _assess(ScreeningCall(stance="HOLD", conviction="LOW"))
    apply_guards(a, _card(), CFG)
    assert a.screening_call.as_of_price == 123.45


def test_none_call_is_noop():
    a = _assess(None)
    apply_guards(a, _card(gates=["negative_fcf"]), CFG)  # must not raise
    assert a.screening_call is None
