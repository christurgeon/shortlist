from shortlist.backtest.prize_bound import topn_overlap, kendall_tau, ranking_from


def test_ranking_from_orders_by_value_desc():
    # higher composite ranks first
    assert ranking_from({"A": 90.0, "B": 50.0, "C": 70.0}) == ["A", "C", "B"]


def test_topn_overlap_identical_rankings():
    a = ranking_from({"A": 9, "B": 8, "C": 7, "D": 6})
    assert topn_overlap(a, a, 2) == 1.0


def test_topn_overlap_one_swapped_out_of_top2():
    a = ["A", "B", "C", "D"]
    b = ["A", "C", "B", "D"]   # B<->C swapped: top-2 is {A,B} vs {A,C} -> 1/2
    assert topn_overlap(a, b, 2) == 0.5


def test_kendall_tau_identical_is_one():
    a = ["A", "B", "C", "D"]
    assert kendall_tau(a, a) == 1.0


def test_kendall_tau_full_reverse_is_minus_one():
    a = ["A", "B", "C", "D"]
    assert kendall_tau(a, list(reversed(a))) == -1.0


def test_kendall_tau_single_swap():
    a = ["A", "B", "C", "D"]
    b = ["B", "A", "C", "D"]   # one adjacent transposition of 6 pairs -> tau = (5-1)/6
    assert abs(kendall_tau(a, b) - (4 / 6)) < 1e-9
