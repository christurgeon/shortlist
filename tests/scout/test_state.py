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


def test_finra_cycle_persists_and_is_backward_compatible(tmp_path):
    path = tmp_path / "state.json"
    st = ScoutState(path)
    assert st.finra_last_settlement() is None       # fresh/old ledger: absent key
    st.set_finra_cycle("2026-06-15")
    fresh = ScoutState(path)                          # persists across instances
    assert fresh.finra_last_settlement() == "2026-06-15"


def test_held_list_filters(tmp_path):
    st = ScoutState(tmp_path / "state.json")
    st.set_held(["TSLA"])
    assert st.is_held("TSLA") is True
    assert st.is_held("AAPL") is False


def test_save_round_trips_atomically_and_leaves_no_temp(tmp_path):
    path = tmp_path / "state.json"
    st = ScoutState(path)
    st.record_screened(["AAPL"], session=date(2026, 6, 1))
    st.mark_run_completed(date(2026, 6, 1))
    # atomic replace: the final file is complete valid JSON, no .tmp sibling left behind
    assert not (tmp_path / "state.json.tmp").exists()
    fresh = ScoutState(path)
    assert fresh.run_completed(date(2026, 6, 1)) is True
    assert fresh.in_cooldown("AAPL", on=date(2026, 6, 2), cooldown_days=7) is True


def test_corrupt_state_preserved_and_fresh_state_returned(tmp_path):
    import pytest
    path = tmp_path / "state.json"
    path.write_text('{"screened": {"AAPL"')          # truncated mid-write
    with pytest.warns(UserWarning, match="corrupt") as w:
        st = ScoutState(path)
    # fresh, usable state — the daily run survives
    assert st.run_completed(date(2026, 6, 1)) is False
    st.mark_run_completed(date(2026, 6, 1))
    assert ScoutState(path).run_completed(date(2026, 6, 1)) is True
    # the corrupt bytes were preserved under a timestamped sibling, named in the warning
    preserved = list(tmp_path.glob("state.json.corrupt-*"))
    assert len(preserved) == 1
    assert preserved[0].read_text() == '{"screened": {"AAPL"'
    assert preserved[0].name in str(w[0].message)


def test_malformed_screened_entry_reads_as_not_in_cooldown(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"screened": {"AAPL": "not-a-date", "MSFT": 7},'
                    ' "runs": [], "held": [], "picks": {}}')
    st = ScoutState(path)
    # one malformed persisted entry must not abort the whole daily run
    assert st.in_cooldown("AAPL", on=date(2026, 6, 1), cooldown_days=7) is False
    assert st.in_cooldown("MSFT", on=date(2026, 6, 1), cooldown_days=7) is False


def test_malformed_yahoo_blocked_until_reads_as_not_blocked(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"screened": {}, "runs": [], "held": [], "picks": {},'
                    ' "yahoo_blocked_until": "garbage"}')
    assert ScoutState(path).yahoo_blocked_on(date(2026, 6, 5)) is False
    path.write_text('{"screened": {}, "runs": [], "held": [], "picks": {},'
                    ' "yahoo_blocked_until": 20260605}')
    assert ScoutState(path).yahoo_blocked_on(date(2026, 6, 5)) is False


def test_position_alerts_seen_roundtrip(tmp_path):
    from shortlist.scout.state import ScoutState
    s = ScoutState(tmp_path / "state.json")
    assert s.position_alerts_seen() == []
    s.add_position_alerts(["8k:0001-1", "8k:0001-2"])
    assert s.position_alerts_seen() == ["8k:0001-1", "8k:0001-2"]
    s.add_position_alerts(["8k:0001-2", "8k:0001-3"])   # dedup
    assert s.position_alerts_seen() == ["8k:0001-1", "8k:0001-2", "8k:0001-3"]


def test_position_alerts_absent_key_back_compat(tmp_path):
    from shortlist.scout.state import ScoutState
    (tmp_path / "state.json").write_text('{"held": []}')   # old file, no key
    s = ScoutState(tmp_path / "state.json")
    assert s.position_alerts_seen() == []


def test_position_alerts_cap_evicts_oldest(tmp_path):
    from shortlist.scout.state import ScoutState
    s = ScoutState(tmp_path / "state.json")
    s.add_position_alerts([f"8k:{i}" for i in range(5)], cap=3)
    assert s.position_alerts_seen() == ["8k:2", "8k:3", "8k:4"]
