"""Tests for backtest/residual.py — per-date partial residualization.

Numeric fixtures below share one base design (2 controls, 4 tickers, dates
d): ctrl1 = [1,2,3,4], ctrl2 = [2,4,1,3]. For the LEVEL-method exact-recovery
test the residual (eps) is a hand-solved null-space vector of the design
matrix [1, ctrl1, ctrl2] — i.e. orthogonal to all three columns by
construction — so OLS must recover it exactly (up to float error) regardless
of the (a, b1, b2) chosen, by the uniqueness of the OLS fitted/residual
orthogonal decomposition. For the RANK-method test, ctrl1/ctrl2/target are
themselves already valid rank vectors (a permutation of 1..4, no ties), so
`metrics.rank` is the identity on them and the expected OLS fit was solved
independently by hand via the normal equations (see comments below) —
verified two ways (direct solve + orthogonal-columns shortcut) before being
hard-coded here.
"""
from __future__ import annotations

from datetime import date

from shortlist.backtest.metrics import rank as ref_rank
from shortlist.backtest.residual import residual_rows
from shortlist.backtest.signals import Observation
from shortlist.scout.validate import _residuals, ols

D = date(2024, 1, 1)


def _obs(vals: dict[str, dict[str, float]]) -> list[Observation]:
    return [Observation(D, tk, sig) for tk, sig in vals.items()]


def test_level_method_exact_recovery_planted_residual():
    # target = 10 + 2*ctrl1 + 3*ctrl2 + eps, eps = (1, -1, -1, 1) is orthogonal
    # to [1, ctrl1, ctrl2] by construction (hand-solved null space) -> OLS must
    # recover the residual exactly.
    obs = _obs({
        "A": {"target": 19.0, "ctrl1": 1.0, "ctrl2": 2.0},
        "B": {"target": 25.0, "ctrl1": 2.0, "ctrl2": 4.0},
        "C": {"target": 18.0, "ctrl1": 3.0, "ctrl2": 1.0},
        "D": {"target": 28.0, "ctrl1": 4.0, "ctrl2": 3.0},
    })
    rows, diag = residual_rows(obs, "target", ["ctrl1", "ctrl2"],
                               min_names=4, method="level")
    assert diag["skipped_floor"] == 0
    assert diag["skipped_singular"] == 0
    assert diag["n_dates"] == 1
    expected = {"A": 1.0, "B": -1.0, "C": -1.0, "D": 1.0}
    got = rows[D]
    assert set(got) == set(expected)
    for tk, exp in expected.items():
        assert abs(got[tk] - exp) < 1e-9
    assert diag["mean_r2"] < 1.0          # real residual variance present
    assert set(diag["beta_std"]) == {"ctrl1", "ctrl2"}


def test_rank_method_hand_computed_residual():
    # ctrl1/ctrl2/target raw values ARE already valid rank permutations of
    # 1..4 (no ties) -> metrics.rank is the identity, so the design fed to OLS
    # is exactly [1, [1,2,3,4], [2,4,1,3]] regressing y=[4,1,3,2]. Hand-solved
    # normal equations give a=5.5, b1=-0.4, b2=-0.8 -> residuals
    # [0.5, -0.5, -0.5, 0.5] (verified independently via the orthogonal-
    # columns shortcut: cov(y,ctrl1)/var(ctrl1) etc. — both agree).
    obs = _obs({
        "A": {"target": 4.0, "ctrl1": 1.0, "ctrl2": 2.0},
        "B": {"target": 1.0, "ctrl1": 2.0, "ctrl2": 4.0},
        "C": {"target": 3.0, "ctrl1": 3.0, "ctrl2": 1.0},
        "D": {"target": 2.0, "ctrl1": 4.0, "ctrl2": 3.0},
    })
    rows, diag = residual_rows(obs, "target", ["ctrl1", "ctrl2"],
                               min_names=4, method="rank")
    assert diag["n_dates"] == 1
    expected = {"A": 0.5, "B": -0.5, "C": -0.5, "D": 0.5}
    got = rows[D]
    for tk, exp in expected.items():
        assert abs(got[tk] - exp) < 1e-9


def test_pure_reencoding_gives_near_zero_residuals_and_r2_near_one():
    # target is an EXACT linear combination of the controls (no noise term) ->
    # OLS fits it perfectly: residuals ~ 0, R^2 ~ 1.0.
    obs = _obs({
        "A": {"target": 8.0, "ctrl1": 1.0, "ctrl2": 2.0},    # 2*1+3*2=8
        "B": {"target": 16.0, "ctrl1": 2.0, "ctrl2": 4.0},   # 2*2+3*4=16
        "C": {"target": 9.0, "ctrl1": 3.0, "ctrl2": 1.0},    # 2*3+3*1=9
        "D": {"target": 17.0, "ctrl1": 4.0, "ctrl2": 3.0},   # 2*4+3*3=17
    })
    rows, diag = residual_rows(obs, "target", ["ctrl1", "ctrl2"],
                               min_names=4, method="level")
    for r in rows[D].values():
        assert abs(r) < 1e-9
    assert abs(diag["mean_r2"] - 1.0) < 1e-9


def test_floor_skip_counted():
    # Only 3 co-present names, default min_names=10 -> the date is skipped and
    # counted, never fit.
    obs = _obs({
        "A": {"target": 1.0, "ctrl1": 1.0, "ctrl2": 2.0},
        "B": {"target": 2.0, "ctrl1": 2.0, "ctrl2": 4.0},
        "C": {"target": 3.0, "ctrl1": 3.0, "ctrl2": 1.0},
    })
    rows, diag = residual_rows(obs, "target", ["ctrl1", "ctrl2"])
    assert rows == {}
    assert diag["skipped_floor"] == 1
    assert diag["skipped_singular"] == 0
    assert diag["n_dates"] == 0


def test_singular_design_counted_never_raises():
    # ctrl2 is an exact duplicate of ctrl1 -> after rank-transform the two
    # design columns are identical -> singular normal-equations matrix.
    obs = _obs({
        "A": {"target": 5.0, "ctrl1": 10.0, "ctrl2": 10.0},
        "B": {"target": 1.0, "ctrl1": 20.0, "ctrl2": 20.0},
        "C": {"target": 9.0, "ctrl1": 30.0, "ctrl2": 30.0},
        "D": {"target": 2.0, "ctrl1": 40.0, "ctrl2": 40.0},
    })
    rows, diag = residual_rows(obs, "target", ["ctrl1", "ctrl2"], min_names=4)
    assert rows == {}
    assert diag["skipped_floor"] == 0
    assert diag["skipped_singular"] == 1
    assert diag["n_dates"] == 0


def test_ties_use_average_rank_matching_spearman_ic_convention():
    # ctrl1 has a tie (two names share the smallest value) -> metrics.rank
    # (spearman_ic's own rank fn, average-tie) governs the transform. Expected
    # is computed independently here by replicating the documented pipeline
    # (rank each column, then ols/_residuals) with the SAME primitives
    # residual_rows is documented to reuse, confirming the tie convention
    # (average, not ordinal/first-occurrence) is actually applied end to end.
    obs = _obs({
        "A": {"target": 7.0, "ctrl1": 10.0, "ctrl2": 2.0},
        "B": {"target": 3.0, "ctrl1": 10.0, "ctrl2": 4.0},   # tied with A on ctrl1
        "C": {"target": 9.0, "ctrl1": 30.0, "ctrl2": 1.0},
        "D": {"target": 1.0, "ctrl1": 40.0, "ctrl2": 3.0},
    })
    tickers = ["A", "B", "C", "D"]
    r_ctrl1 = ref_rank([10.0, 10.0, 30.0, 40.0])
    assert r_ctrl1 == [1.5, 1.5, 3.0, 4.0]     # average-tie sanity check
    r_ctrl2 = ref_rank([2.0, 4.0, 1.0, 3.0])
    r_target = ref_rank([7.0, 3.0, 9.0, 1.0])
    X = [list(row) for row in zip(r_ctrl1, r_ctrl2)]
    b = ols(r_target, X)
    expected_resid = _residuals(r_target, X, b)

    rows, diag = residual_rows(obs, "target", ["ctrl1", "ctrl2"], min_names=4)
    got = rows[D.__class__(2024, 1, 1)]
    for tk, exp in zip(tickers, expected_resid, strict=True):
        assert abs(got[tk] - exp) < 1e-9
    assert diag["n_dates"] == 1


def test_none_signal_tickers_dropped_from_cocount_and_output():
    # A 5th ticker with target=None must not count toward min_names, must not
    # appear in the output row, and the remaining 4 names should fit exactly
    # as in the hand-computed rank test above.
    obs = _obs({
        "A": {"target": 4.0, "ctrl1": 1.0, "ctrl2": 2.0},
        "B": {"target": 1.0, "ctrl1": 2.0, "ctrl2": 4.0},
        "C": {"target": 3.0, "ctrl1": 3.0, "ctrl2": 1.0},
        "D": {"target": 2.0, "ctrl1": 4.0, "ctrl2": 3.0},
        "E": {"target": None, "ctrl1": 5.0, "ctrl2": 5.0},
    })
    rows, diag = residual_rows(obs, "target", ["ctrl1", "ctrl2"],
                               min_names=4, method="rank")
    assert diag["skipped_floor"] == 0
    got = rows[D]
    assert set(got) == {"A", "B", "C", "D"}
    expected = {"A": 0.5, "B": -0.5, "C": -0.5, "D": 0.5}
    for tk, exp in expected.items():
        assert abs(got[tk] - exp) < 1e-9


def test_none_control_ticker_dropped():
    obs = _obs({
        "A": {"target": 4.0, "ctrl1": 1.0, "ctrl2": 2.0},
        "B": {"target": 1.0, "ctrl1": 2.0, "ctrl2": 4.0},
        "C": {"target": 3.0, "ctrl1": 3.0, "ctrl2": 1.0},
        "D": {"target": 2.0, "ctrl1": 4.0, "ctrl2": 3.0},
        "F": {"target": 6.0, "ctrl1": 5.0, "ctrl2": None},   # ctrl2 missing
    })
    rows, diag = residual_rows(obs, "target", ["ctrl1", "ctrl2"],
                               min_names=4, method="rank")
    got = rows[D]
    assert "F" not in got
    assert set(got) == {"A", "B", "C", "D"}


def test_empty_observations_returns_zeroed_diagnostics():
    rows, diag = residual_rows([], "target", ["ctrl1", "ctrl2"])
    assert rows == {}
    assert diag == {
        "skipped_floor": 0,
        "skipped_singular": 0,
        "n_dates": 0,
        "mean_r2": 0.0,
        "beta_std": {"ctrl1": 0.0, "ctrl2": 0.0},
    }
