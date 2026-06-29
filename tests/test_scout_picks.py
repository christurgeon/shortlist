from datetime import date

from shortlist.scout.picks import Pick, pick_from_card
from shortlist.scout.state import ScoutState


def _pick(t, gated=False):
    return Pick(ticker=t, cik="0000000001", session="2026-06-18",
                catalyst="edgar:activist_13d", evidence="Activist 13D: Elliott → " + t,
                composite=72.0, confidence=0.8, sic_bucket="industrials",
                as_of_price=10.0, market_cap=1e9, gated=gated)


def test_pick_to_dict_roundtrip():
    d = _pick("XYZ").to_dict()
    assert d["ticker"] == "XYZ" and d["as_of_price"] == 10.0 and d["gated"] is False
    assert d["cik"] == "0000000001"


def test_pick_from_card_carries_cik_catalyst_and_price():
    from datetime import date as _date

    from shortlist.models import ScoreCard, StockMetrics
    from shortlist.scout.models import Candidate, Emission

    cand = Candidate(ticker="XYZ")
    cand.emissions = [Emission("XYZ", "edgar:activist_13d", 0.9, "Activist 13D: Elliott → XYZ",
                               is_discovery=True, cik="0001326200")]
    card = ScoreCard(ticker="XYZ", composite=70.0, quality=None, moat=None, growth=None,
                     momentum=None, value=None, opportunity=None, insider=None,
                     gates=["over_leveraged"],
                     metrics=StockMetrics(ticker="XYZ", price=12.5, market_cap=2e9))
    p = pick_from_card(card, cand, _date(2026, 6, 18))
    assert p.cik == "0001326200"
    assert p.catalyst == "edgar:activist_13d"
    assert p.as_of_price == 12.5 and p.market_cap == 2e9
    assert p.gated is True   # gated picks are recorded too (raw-signal measurement)


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
