from shortlist.data.models import TickerSnapshot, GovContracts, Statements
from shortlist.data.bridge import snapshot_to_metrics


def _snap(**gc):
    s = TickerSnapshot(ticker="LMT", as_of="2026-06-15")
    s.gov_contracts = GovContracts(as_of="2026-06-10", **gc)
    return s


def test_bridge_derives_yoy_and_count():
    m = snapshot_to_metrics(_snap(ttm_obligated=1.2e10, prior_ttm_obligated=1.0e10,
                                  award_count_ttm=42, match_confidence=0.98))
    assert m.gov_contract_ttm_usd == 1.2e10
    assert abs(m.gov_contract_yoy_growth - 0.2) < 1e-9
    assert m.gov_contract_award_count == 42
    assert m.gov_contract_match_confidence == 0.98
    assert m.gov_contract_data_age_days == 5


def test_bridge_yoy_none_when_prior_zero_or_missing():
    assert snapshot_to_metrics(_snap(ttm_obligated=5e9, prior_ttm_obligated=0.0)
                               ).gov_contract_yoy_growth is None
    assert snapshot_to_metrics(_snap(ttm_obligated=5e9)
                               ).gov_contract_yoy_growth is None


def test_bridge_to_revenue_only_with_revenue():
    # No revenue on a bare snapshot -> ratio stays None.
    assert snapshot_to_metrics(_snap(ttm_obligated=5e9)
                               ).gov_contract_to_revenue is None


def test_bridge_no_section_leaves_fields_none():
    m = snapshot_to_metrics(TickerSnapshot(ticker="KO"))
    assert m.gov_contract_ttm_usd is None


def test_bridge_to_revenue_with_revenue():
    s = TickerSnapshot(ticker="LMT", as_of="2026-06-15")
    s.gov_contracts = GovContracts(as_of="2026-06-10", ttm_obligated=3e10)
    s.statements = Statements(revenue=[6e10])  # newest-first; bridge sets m.revenue
    m = snapshot_to_metrics(s)
    assert m.gov_contract_to_revenue is not None
    assert abs(m.gov_contract_to_revenue - 0.5) < 1e-9
