from datetime import date

from shortlist import positions as pos
from shortlist.scout import daily
from shortlist.scout.state import ScoutState


def test_build_monitor_payload_filters_and_dedups(tmp_path):
    store = {"version": 1, "positions": {
        "NVDA": {"added": "2026-01-01", "shares": 10, "thesis": "t", "entry_card": None}}}
    pos.save_store(tmp_path / "positions.json", store)
    state = ScoutState(tmp_path / "state.json")
    veto_map = {"NVDA": {"items": ["4.02"], "adsh": "AAA", "last_date": "2026-07-19"},
                "MSFT": {"items": ["4.02"], "adsh": "BBB", "last_date": "2026-07-19"}}  # unheld
    payload = daily._build_monitor_payload(
        str(tmp_path / "positions.json"), veto_map,
        items=("1.03", "2.04", "4.02"), state=state, session=date(2026, 7, 22))
    assert payload["heartbeat"]["count"] == 1
    assert [a["ticker"] for a in payload["alerts"]] == ["NVDA"]   # MSFT filtered (unheld)


def test_monitor_payload_none_when_disabled():
    assert daily._build_monitor_payload_if_enabled(
        {"portfolio": {"monitor": {"enabled": False}}}, veto_map={}, state=None,
        session=date(2026, 7, 22)) is None


def test_persist_marks_alerts_seen(tmp_path):
    state = ScoutState(tmp_path / "state.json")
    payload = {"alerts": [{"key": "8k:AAA"}, {"key": "8k:BBB"}], "heartbeat": {}}
    daily._persist_monitor(state, payload)
    assert set(state.position_alerts_seen()) == {"8k:AAA", "8k:BBB"}
