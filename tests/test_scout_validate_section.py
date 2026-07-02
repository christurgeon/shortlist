from shortlist.scout.report.sections import _ValidationScoreboard


def test_validation_section_absent_when_no_verdicts():
    vm = type("VM", (), {"validation": None})()
    assert _ValidationScoreboard().applies(vm) is False


def test_validation_section_renders_disclaimer_and_never_says_promote():
    vm = type("VM", (), {"validation": [
        {"signal": "edgar:activist_13d", "verdict": "INSUFFICIENT", "ir": None,
         "n_measurable": 3, "n_selected": 3}]})()
    sec = _ValidationScoreboard()
    assert sec.applies(vm) is True
    text = sec.render_text(vm)
    assert "not evidence" in text.lower() or "not advice" in text.lower()
    assert "PROMOTE" not in text.upper()
