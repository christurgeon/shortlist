from shortlist.scout.models import Candidate, Emission
from shortlist.scout.budget import select


def _cand(ticker, interest):
    c = Candidate(ticker=ticker)
    c.add(Emission(ticker, "yahoo:day_gainers", interest, "", True), 1.0)
    return c


def test_select_takes_top_x_by_interest():
    cands = [_cand("A", 0.2), _cand("B", 0.9), _cand("C", 0.5), _cand("D", 0.7)]
    chosen, dropped, capped = select(cands, daily_x=2)
    assert [c.ticker for c in chosen] == ["B", "D"]
    assert dropped == 2


def test_select_under_cap_drops_nothing():
    cands = [_cand("A", 0.2), _cand("B", 0.9)]
    chosen, dropped, capped = select(cands, daily_x=5)
    assert len(chosen) == 2 and dropped == 0
