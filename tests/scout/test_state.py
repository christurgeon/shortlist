from datetime import date
from shortlist.scout.state import ScoutState


def test_cooldown_blocks_recently_screened(tmp_path):
    st = ScoutState(tmp_path / "state.json")
    st.record_screened(["AAPL", "MSFT"], session=date(2026, 5, 29))
    assert st.in_cooldown("AAPL", on=date(2026, 6, 1), cooldown_days=7) is True
    assert st.in_cooldown("AAPL", on=date(2026, 6, 10), cooldown_days=7) is False
    assert st.in_cooldown("NVDA", on=date(2026, 6, 1), cooldown_days=7) is False


def test_run_completed_marker_is_idempotent(tmp_path):
    path = tmp_path / "state.json"
    st = ScoutState(path)
    assert st.run_completed(date(2026, 5, 29)) is False
    st.mark_run_completed(date(2026, 5, 29))
    # fresh instance reads from disk
    assert ScoutState(path).run_completed(date(2026, 5, 29)) is True


def test_held_list_filters(tmp_path):
    st = ScoutState(tmp_path / "state.json")
    st.set_held(["TSLA"])
    assert st.is_held("TSLA") is True
    assert st.is_held("AAPL") is False
