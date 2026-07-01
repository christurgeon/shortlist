import json
from datetime import date
from shortlist.scout.state import ScoutState
from shortlist.scout.firehose import cohort_events_from_emissions
from shortlist.scout.models import Emission


def _events(session):
    return cohort_events_from_emissions(
        [Emission("ABC", "edgar:activist_13d", 0.9, "ev", True, "0000123")], session)


def test_record_and_read_firehose(tmp_path):
    st = ScoutState(tmp_path / "s.json")
    st.record_firehose(_events(date(2026, 7, 1)), date(2026, 7, 1))
    rows = st.firehose_events(on=date(2026, 7, 1), lookback_days=30)
    assert len(rows) == 1
    assert rows[0]["signal"] == "edgar:activist_13d"
    assert rows[0]["event_date"] == "2026-07-01"


def test_firehose_key_absent_on_untouched_state(tmp_path):
    """Byte-identical guarantee: creating/saving state without ever calling record_firehose
    must NOT introduce a 'firehose' key (old-file diffs stay minimal)."""
    st = ScoutState(tmp_path / "s.json")
    st.mark_run_completed(date(2026, 7, 1))     # triggers a _save via an existing path
    saved = json.loads((tmp_path / "s.json").read_text())
    assert "firehose" not in saved


def test_old_state_file_without_firehose_loads(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"screened": {}, "runs": [], "held": [], "picks": {}}))
    st = ScoutState(p)                            # must not raise
    assert st.firehose_events(on=date(2026, 7, 1), lookback_days=30) == []
    st.record_firehose(_events(date(2026, 7, 1)), date(2026, 7, 1))   # upsert onto old file
    assert len(st.firehose_events(on=date(2026, 7, 1), lookback_days=30)) == 1
