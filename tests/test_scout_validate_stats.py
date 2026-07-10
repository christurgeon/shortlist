from shortlist.scout.validate import (
    ols,
    ff3_alpha,
    effective_blocks,
    information_ratio,
    stationary_block_bootstrap_alpha,
)


def _ff3(months=None):
    """FF3 map with small deterministic NON-ZERO variance on all three factors so X'X is
    well-conditioned (pure OLS, no ridge). R_p is still built with NO smb/hml loading in the
    tests below, so alpha / b_mkt are recovered exactly and b_smb ~ b_hml ~ 0."""
    if months is None:
        months = range(1, 13)
    return {
        f"2025-{m:02d}": (
            0.01 * ((m % 3) - 1),      # mkt   (period-3 pattern)
            0.001 * ((m % 2) - 1),     # smb   (period-2 — independent of mkt)
            0.002 * ((m % 4) - 1.5),   # hml   (period-4 — independent of both)
            0.003,                     # rf
        )
        for m in months
    }


def test_ols_recovers_known_line():
    # y = 2 + 3*x1 - 1*x2 exactly -> intercept 2, coeffs [3, -1]
    X = [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0], [2.0, 1.0], [1.0, 2.0]]
    y = [2 + 3*a - 1*b for a, b in X]
    b = ols(y, X)
    assert abs(b[0] - 2.0) < 1e-6
    assert abs(b[1] - 3.0) < 1e-6
    assert abs(b[2] + 1.0) < 1e-6


def test_ff3_alpha_zero_when_returns_are_pure_factor_exposure():
    # R_p = rf + 1.0*mkt (pure market beta, no alpha, no smb/hml loading) -> alpha ~ 0, b_mkt ~ 1
    ff3 = _ff3()
    ctp = [(mo, rf + 1.0 * mkt, 1) for mo, (mkt, smb, hml, rf) in ff3.items()]
    alpha, betas = ff3_alpha(ctp, ff3)
    assert abs(alpha) < 1e-6
    assert abs(betas[0] - 1.0) < 1e-6
    assert abs(betas[1]) < 1e-6           # b_smb ~ 0 (no smb loading)
    assert abs(betas[2]) < 1e-6           # b_hml ~ 0 (no hml loading)


def test_ff3_alpha_positive_when_constant_excess_added():
    ff3 = _ff3()
    ctp = [(mo, rf + 1.0 * mkt + 0.02, 1) for mo, (mkt, smb, hml, rf) in ff3.items()]
    alpha, betas = ff3_alpha(ctp, ff3)
    assert abs(alpha - 0.02) < 1e-6         # 2%/month alpha recovered


def test_ff3_alpha_none_below_min_obs():
    ff3 = {"2025-01": (0.01, 0.0, 0.0, 0.003)}
    assert ff3_alpha([("2025-01", 0.05, 1)], ff3) == (None, [])


def test_effective_blocks_is_independent_blocks_not_months():
    assert effective_blocks(24, 12) == 2      # NOT 24
    assert effective_blocks(11, 12) == 0


def test_information_ratio_finite_for_real_alpha():
    ff3 = _ff3()
    ctp = [(mo, rf + 1.0 * mkt + 0.01 + 0.001 * ((m := int(mo[-2:])) - 6), 1)
           for mo, (mkt, smb, hml, rf) in ff3.items()]
    ir = information_ratio(ctp, ff3)
    assert ir is not None and ir > 0


def test_block_bootstrap_ci_brackets_true_alpha_and_is_deterministic():
    # ~48 months of a clean 2%/mo alpha over the varying (mkt, smb, hml) factors.
    ff3 = {
        f"{2022 + (i // 12)}-{(i % 12) + 1:02d}": (
            0.01 * ((i % 3) - 1),
            0.001 * ((i % 2) - 1),
            0.002 * ((i % 4) - 1.5),
            0.003,
        )
        for i in range(48)
    }
    ctp = [(mo, rf + 1.0 * mkt + 0.02, 1) for mo, (mkt, smb, hml, rf) in ff3.items()]

    ci = stationary_block_bootstrap_alpha(ctp, ff3, k_months=3, n_boot=500, seed=12345)
    assert ci is not None
    lo, hi = ci
    assert lo < hi                       # (a) ordered CI
    assert lo < 0.02 < hi                # (b) brackets the true 0.02 alpha

    # (c) deterministic — same seed -> identical CI
    ci2 = stationary_block_bootstrap_alpha(ctp, ff3, k_months=3, n_boot=500, seed=12345)
    assert ci == ci2
