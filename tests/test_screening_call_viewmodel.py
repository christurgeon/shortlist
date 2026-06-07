from shortlist.scout.report.viewmodel import _assessment_vm, call_one_liner


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
