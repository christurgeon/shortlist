import random

from shortlist.backtest.metrics import rank, spearman_ic, aggregate_ic, quantile_spread


def test_rank_averages_ties():
    assert rank([10, 20, 30]) == [1.0, 2.0, 3.0]
    assert rank([10, 10, 30]) == [1.5, 1.5, 3.0]      # tie -> average rank
    assert rank([30, 10, 20]) == [3.0, 1.0, 2.0]


def test_spearman_perfect_and_reversed():
    sig = [1, 2, 3, 4, 5]
    assert spearman_ic(sig, [10, 20, 30, 40, 50]) == 1.0
    assert spearman_ic(sig, [50, 40, 30, 20, 10]) == -1.0


def test_spearman_with_ties_matches_hand_value():
    ic = spearman_ic([1, 1, 3], [5, 6, 7])
    assert ic is not None and abs(ic - 0.8660254) < 1e-6


def test_spearman_noise_near_zero():
    rng = random.Random(42)
    sig = [rng.random() for _ in range(500)]
    fwd = [rng.random() for _ in range(500)]
    assert abs(spearman_ic(sig, fwd)) < 0.1


def test_spearman_drops_none_pairs_and_needs_three():
    assert spearman_ic([1, None, 3, 4], [10, 99, 30, 40]) == 1.0
    assert spearman_ic([1, 2], [1, 2]) is None


def test_aggregate_ic_known_values():
    s = aggregate_ic([0.1, 0.2, 0.3])
    assert abs(s.mean - 0.2) < 1e-9
    assert s.n == 3
    assert s.hit_rate == 1.0
    assert abs(s.std - 0.1) < 1e-9            # SAMPLE stdev (n-1), not population
    assert abs(s.icir - (0.2 / s.std)) < 1e-9
    assert abs(s.t_stat - s.icir * (3 ** 0.5)) < 1e-9


def test_aggregate_ic_hit_rate_mixed():
    s = aggregate_ic([0.1, -0.1, 0.1, 0.1])
    assert s.hit_rate == 0.75


def test_quantile_spread_monotonic():
    pairs = [(float(i), float(i)) for i in range(1, 11)]
    q = quantile_spread(pairs, 5)
    assert q.spread > 0
    assert q.monotonic is True
    assert q.n_buckets == 5


def test_quantile_spread_collapses_when_small():
    pairs = [(float(i), float(i)) for i in range(1, 7)]   # 6 obs
    q = quantile_spread(pairs, 5)
    assert q.n_buckets <= 3                                # auto-collapse
