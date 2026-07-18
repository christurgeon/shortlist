from pathlib import Path

from shortlist.scout.quality import is_13d_amendment, is_initial_13d
from shortlist.scout.state import ScoutState


def test_amendment_predicate():
    assert is_13d_amendment("SCHEDULE 13D/A")
    assert is_13d_amendment("SC 13D/A")
    assert is_13d_amendment(" schedule 13d/a ")
    assert not is_13d_amendment("SCHEDULE 13D")
    assert not is_13d_amendment("SC 13D")
    assert not is_13d_amendment("")
    assert not is_initial_13d("SCHEDULE 13D/A")   # disjoint by construction


def test_baselines_roundtrip_newest_wins_and_cap(tmp_path: Path):
    st = ScoutState(tmp_path / "state.json")
    st.update_stake_baselines({"a|b": {"pct": 5.0, "date": "2026-01-02"}})
    st.update_stake_baselines({"a|b": {"pct": 4.0, "date": "2026-01-01"}})  # older: ignored
    assert ScoutState(tmp_path / "state.json").stake_baselines()["a|b"]["pct"] == 5.0
    st.update_stake_baselines({"a|b": {"pct": 7.5, "date": "2026-02-01"}})  # newer: wins
    assert st.stake_baselines()["a|b"]["pct"] == 7.5
    st.update_stake_baselines(
        {f"k{i}|s": {"pct": 1.0, "date": "2026-03-01"} for i in range(3)}, cap=2)
    m = st.stake_baselines()
    assert len(m) == 2 and "a|b" not in m         # oldest-insertion evicted


def test_seen_accessions_capped(tmp_path: Path):
    st = ScoutState(tmp_path / "state.json")
    st.add_stake_increase_accessions(["x1", "x2"])
    st.add_stake_increase_accessions(["x2", "x3"], cap=2)
    assert ScoutState(tmp_path / "state.json").stake_increase_seen_accessions() == ["x2", "x3"]


def test_old_state_files_forward_compatible(tmp_path: Path):
    p = tmp_path / "state.json"
    p.write_text('{"screened": {}, "runs": [], "held": []}')   # pre-feature state
    st = ScoutState(p)
    assert st.stake_baselines() == {}
    assert st.stake_increase_seen_accessions() == []
