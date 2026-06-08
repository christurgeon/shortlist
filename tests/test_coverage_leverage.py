from shortlist.coverage import _SUBSCORE_FIELDS


def test_leverage_fields_not_in_subscore_coverage():
    # New leverage metrics are gate inputs / surfaced fields, NOT sub-scores — they
    # must never enter the coverage.unavailable accounting (which would inflate the
    # `thin` advisory for FMP-gated large-caps).
    for f in ("revenue", "ebitda", "cash_and_equivalents", "net_debt_to_ebitda"):
        assert f not in _SUBSCORE_FIELDS
