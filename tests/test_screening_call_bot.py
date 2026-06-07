from shortlist.scout.bot import _call_summary


def test_call_summary_builds_message():
    assessments = {
        "X": {"screening_call": {"stance": "BUY", "conviction": "MEDIUM", "rationale": "r"},
              "thesis": {"what_would_change_my_mind": ["margins compress"]}},
        "Y": {"screening_call": {"stance": "AVOID", "conviction": "LOW", "rationale": "r"},
              "thesis": {}},
    }
    msg = _call_summary(assessments)
    assert msg is not None
    assert "not advice" in msg
    assert "X" in msg and "Buy" in msg and "margins compress" in msg
    assert "Y" in msg and "Avoid" in msg


def test_call_summary_none_when_no_calls():
    assert _call_summary({"X": {"thesis": {}}}) is None
    assert _call_summary({}) is None
    assert _call_summary({"X": "not-a-dict"}) is None   # non-dict record skipped
