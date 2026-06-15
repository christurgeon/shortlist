from shortlist.models import StockMetrics


def test_lobbying_fields_default_none():
    m = StockMetrics(ticker="LMT")
    assert m.lobbying_ttm_usd is None
    assert m.lobbying_prior_ttm_usd is None
    assert m.lobbying_yoy_growth is None
    assert m.lobbying_filing_count is None
    assert m.lobbying_registrant_count is None
    assert m.lobbying_match_confidence is None
    assert m.lobbying_truncated is None
    assert m.lobbying_total_filings is None
    assert m.lobbying_data_age_days is None
