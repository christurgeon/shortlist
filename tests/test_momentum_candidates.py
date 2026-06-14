from shortlist.data.sources import mom_6m, mom_12_1, _YH_SIX_MONTHS


def test_mom_6m_is_trailing_six_month_return():
    xs = [1.0] * 300
    xs[-1] = 1.5
    xs[-1 - _YH_SIX_MONTHS] = 1.0   # _YH_SIX_MONTHS == 126
    assert mom_6m(xs) == 0.5


def test_mom_12_1_spans_252_days_ending_at_skip_point():
    # The 12-1 formation window MUST be 252 trading days ending ~21 days back.
    xs = [1.0] * 300
    xs[-22] = 1.30     # numerator (skip the last ~21 days)
    xs[-274] = 1.00    # denominator: 274 - 22 == 252 td span
    assert round(mom_12_1(xs), 6) == 0.30


def test_mom_12_1_needs_274_closes():
    assert mom_12_1([1.0] * 273) is None
    assert mom_12_1([1.0] * 274) is not None


def test_mom_6m_needs_127_closes():
    assert mom_6m([1.0] * 126) is None
    assert mom_6m([1.0] * 127) is not None
