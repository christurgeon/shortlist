from shortlist.models import StockMetrics


def test_gov_contract_fields_default_none():
    m = StockMetrics(ticker="LMT")
    assert m.gov_contract_ttm_usd is None
    assert m.gov_contract_yoy_growth is None
    assert m.gov_contract_to_revenue is None
    assert m.gov_contract_match_confidence is None
    assert m.gov_contract_data_age_days is None
    assert m.gov_contract_award_count is None
    assert m.gov_contract_prior_ttm_usd is None
