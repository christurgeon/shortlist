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


def test_yahoo_cooldown_rest_of_day(tmp_path):
    path = tmp_path / "state.json"
    st = ScoutState(path)
    block_day = date(2026, 6, 5)
    assert st.yahoo_blocked_on(block_day) is False        # nothing recorded yet
    st.mark_yahoo_blocked(block_day)
    # same-day re-runs skip; the next day resumes. Persists across instances.
    fresh = ScoutState(path)
    assert fresh.yahoo_blocked_on(block_day) is True
    assert fresh.yahoo_blocked_on(date(2026, 6, 6)) is False
    assert fresh.yahoo_blocked_until() == "2026-06-05"


def test_yahoo_cooldown_absent_key_is_backward_compatible(tmp_path):
    st = ScoutState(tmp_path / "state.json")  # no yahoo key in a fresh/old ledger
    assert st.yahoo_blocked_on(date(2026, 6, 5)) is False
    assert st.yahoo_blocked_until() is None


def test_held_list_filters(tmp_path):
    st = ScoutState(tmp_path / "state.json")
    st.set_held(["TSLA"])
    assert st.is_held("TSLA") is True
    assert st.is_held("AAPL") is False
