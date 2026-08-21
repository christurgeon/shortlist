from shortlist.bot.report.viewmodel import _assessment_vm, call_one_liner


_REC = {
    "business_model_summary": "Sells widgets.",
    "moat": {"summary": "Scale."},
    "thesis": {"bull_case": "b", "bear_case": "leverage",
               "what_would_change_my_mind": ["margins compress"], "takeaway": "t"},
    "reconciliation": [], "risks": [], "red_flags": [],
    "management_capital_allocation": "",
    "screening_call": {"stance": "BUY", "conviction": "MEDIUM", "rationale": "Durable.",
                       "decided_without": ["value axis — FMP gated this symbol (402)"]},
}


def test_assessment_vm_call_fields():
    vm = _assessment_vm(_REC)
    assert vm.call_stance == "BUY"
    assert vm.call_label == "Buy"
    assert vm.call_conviction == "MEDIUM"
    assert vm.call_rationale == "Durable."
    assert vm.call_watch == "margins compress"
    assert vm.call_decided_without == ["value axis — FMP gated this symbol (402)"]


def test_no_call_all_fields_empty():
    rec = {k: v for k, v in _REC.items() if k != "screening_call"}
    vm = _assessment_vm(rec)
    assert vm.call_stance == "" and vm.call_label == ""
    assert vm.call_conviction == "" and vm.call_rationale == ""
    assert vm.call_watch == "" and vm.call_decided_without == []


def test_non_dict_screening_call_is_ignored():
    rec = {**_REC, "screening_call": "bad-value"}
    vm = _assessment_vm(rec)
    assert vm.call_stance == ""
    assert call_one_liner(rec) is None


def test_call_one_liner():
    assert call_one_liner(_REC) == "Buy · conviction Medium — but watch: margins compress"
    rec = {k: v for k, v in _REC.items() if k != "screening_call"}
    assert call_one_liner(rec) is None


def test_null_conviction_is_safe():
    rec = {"screening_call": {"stance": "BUY", "conviction": None, "rationale": "r"},
           "thesis": {}}
    vm = _assessment_vm(rec)
    assert vm.call_conviction == ""          # no crash, empty not None
    assert vm.call_conviction.title() == ""  # downstream .title() is safe


def test_clamped_call_surfaces_clamp_reason_not_bull_rationale():
    rec = {"screening_call": {"stance": "AVOID", "conviction": "MEDIUM",
                              "rationale": "compelling bull thesis",
                              "stance_clamped": True,
                              "clamp_note": "tripped negative_fcf gate"},
           "thesis": {}}
    vm = _assessment_vm(rec)
    assert "Auto-downgraded" in vm.call_rationale
    assert "tripped negative_fcf gate" in vm.call_rationale
    assert "compelling bull" not in vm.call_rationale


# --- pre-clamp stance (PLAN_INVENTORY_DECOMPOSITION §2) ---------------------------

def test_one_liner_marks_a_gate_override():
    """The bot reply is often the ONLY surface a user sees. "Avoid · conviction Low"
    hid that a gate had overruled the model."""
    from shortlist.bot.report.viewmodel import call_one_liner
    rec = {"thesis": {"what_would_change_my_mind": ["margins compress"]},
           "screening_call": {"stance": "AVOID", "conviction": "LOW",
                              "stance_clamped": True, "model_stance": "HOLD",
                              "clamp_note": "tripped negative_fcf gate"}}
    line = call_one_liner(rec)
    assert "gate override — model said Hold" in line
    assert "conviction Low" in line


def test_one_liner_unchanged_when_nothing_was_clamped():
    """Back-compat: a clean call, and any brief written before model_stance existed,
    must render exactly as before."""
    from shortlist.bot.report.viewmodel import call_one_liner
    rec = {"thesis": {"what_would_change_my_mind": ["margins compress"]},
           "screening_call": {"stance": "BUY", "conviction": "MEDIUM"}}
    assert call_one_liner(rec) == "Buy · conviction Medium — but watch: margins compress"
