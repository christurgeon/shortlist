from shortlist.stats import gross_margin_stability


def test_stable_margins_score_near_one():
    # near-identical margins => very low dispersion => stability ~1.0
    s = gross_margin_stability([0.40, 0.41, 0.40, 0.39])
    assert s is not None and 0.95 < s <= 1.0


def test_volatile_margins_score_lower():
    s = gross_margin_stability([0.10, 0.50, 0.20, 0.45])
    assert s is not None and s < 0.7


def test_fewer_than_three_returns_none():
    assert gross_margin_stability([0.4, 0.4]) is None


def test_zero_mean_returns_none():
    assert gross_margin_stability([0.0, 0.0, 0.0]) is None


def test_never_negative():
    # huge dispersion would push 1 - stdev/mean below 0; clamp to 0.0
    assert gross_margin_stability([0.01, 0.99, 0.02]) == 0.0
