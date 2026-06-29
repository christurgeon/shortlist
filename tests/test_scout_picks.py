from datetime import date

from shortlist.scout.picks import Pick
from shortlist.scout.state import ScoutState


def _pick(t, gated=False):
    return Pick(ticker=t, cik="0000000001", session="2026-06-18", filing_date="2026-06-17",
                catalyst="edgar:activist_13d", evidence="Activist 13D: Elliott → " + t,
                composite=72.0, confidence=0.8, sic_bucket="industrials",
                as_of_price=10.0, market_cap=1e9, gated=gated)


def test_pick_to_dict_roundtrip():
    d = _pick("XYZ").to_dict()
    assert d["ticker"] == "XYZ" and d["as_of_price"] == 10.0 and d["gated"] is False


def test_picks_forward_compatible_with_old_state(tmp_path):
    p = tmp_path / "s.json"
    p.write_text('{"screened": {}, "runs": [], "held": []}')   # pre-picks shape
    st = ScoutState(p)
    st.record_picks([_pick("XYZ")], date(2026, 6, 18))          # must not KeyError
    got = st.recent_picks(date(2026, 6, 18), 120)
    assert [g["ticker"] for g in got] == ["XYZ"]


def test_record_picks_upsert_no_dup(tmp_path):
    st = ScoutState(tmp_path / "s.json")
    st.record_picks([_pick("XYZ")], date(2026, 6, 18))
    st.record_picks([_pick("XYZ")], date(2026, 6, 18))          # same (ticker, session)
    assert len(st.recent_picks(date(2026, 6, 18), 120)) == 1


def test_recent_picks_respects_lookback(tmp_path):
    st = ScoutState(tmp_path / "s.json")
    st.record_picks([_pick("OLD")], date(2026, 1, 1))
    st.record_picks([_pick("NEW")], date(2026, 6, 18))
    recent = st.recent_picks(date(2026, 6, 18), lookback_days=30)
    assert [r["ticker"] for r in recent] == ["NEW"]


def test_record_gated_pick_persisted(tmp_path):
    st = ScoutState(tmp_path / "s.json")
    st.record_picks([_pick("GATED", gated=True)], date(2026, 6, 18))
    got = st.recent_picks(date(2026, 6, 18), 120)
    assert got[0]["gated"] is True
