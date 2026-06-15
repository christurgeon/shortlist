from shortlist.data.models import TickerSnapshot, Lobbying
from shortlist.data.bridge import snapshot_to_metrics


def _snap(**lb):
    s = TickerSnapshot(ticker="LMT", as_of="2026-06-15")
    s.lobbying = Lobbying(as_of="2026-06-15", **lb)
    return s


def test_bridge_derives_yoy_and_passthrough():
    m = snapshot_to_metrics(_snap(latest_filing="2026-06-10", ttm_spend=1.3e7,
                                  prior_ttm_spend=1.0e7, filing_count_ttm=60,
                                  registrant_count=22, match_confidence=0.99,
                                  truncated=True, total_filings=66))
    assert m.lobbying_ttm_usd == 1.3e7
    assert abs(m.lobbying_yoy_growth - 0.3) < 1e-9
    assert m.lobbying_filing_count == 60
    assert m.lobbying_registrant_count == 22
    assert m.lobbying_truncated is True
    assert m.lobbying_total_filings == 66
    assert m.lobbying_data_age_days == 5


def test_bridge_yoy_none_when_prior_zero_or_missing():
    assert snapshot_to_metrics(_snap(ttm_spend=5e6, prior_ttm_spend=0.0)
                               ).lobbying_yoy_growth is None
    assert snapshot_to_metrics(_snap(ttm_spend=5e6)).lobbying_yoy_growth is None


def test_bridge_no_section_leaves_fields_none():
    m = snapshot_to_metrics(TickerSnapshot(ticker="KO"))
    assert m.lobbying_ttm_usd is None
