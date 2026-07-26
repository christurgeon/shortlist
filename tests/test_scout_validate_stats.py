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


# --- event-level bootstrap CI (audit 2026-07-26 §3a) ---------------------------------
# The month-resampled `stationary_block_bootstrap_alpha` resamples an ALREADY-FLATTENED
# CTP series: `calendar_time_portfolio` replaces each event's whole K-month path with a
# constant monthly rate, then averages across held names. Cross-sectional dispersion in
# event outcomes is therefore averaged away BEFORE the bootstrap sees it, so the CI
# reports only "how smooth is this smooth series" — which is how the committed verdicts
# ended up with an implied monthly tracking error of 0.32% and an IR of -46.97.
# The dominant uncertainty is WHICH EVENTS you happened to catch, so the CI must be
# resampled over events.

def _spread_cohort(rets, k_months=3):
    """One event per (month, ticker) spread over 24 months, returns cycled from `rets`."""
    from datetime import date
    from shortlist.scout.validate import MeasuredEvent
    out = []
    for i in range(len(rets)):
        y = 2025 + (i // 12)
        mo = (i % 12) + 1
        r = rets[i]
        out.append(MeasuredEvent("s", f"T{i}", date(y, mo, 10), r, True, 0.5, False, 60.0))
    return out


def test_event_bootstrap_ci_is_wider_than_month_resampled_ci_on_a_dispersed_cohort():
    """Same mean event return, huge cross-sectional dispersion: the event-level CI must
    reflect the dispersion; the month-resampled CI structurally cannot see it."""
    from shortlist.scout.validate import (
        calendar_time_portfolio, event_bootstrap_alpha,
    )
    # 24 events, alternating +60% / -40% over K=3 -- same pooled mean each month, but
    # an enormous spread in which events a resample happens to draw.
    events = _spread_cohort([0.60 if i % 2 == 0 else -0.40 for i in range(24)])
    ff3 = _ff3(range(1, 13))
    ff3.update({f"2026-{m:02d}": v for m, v in
                zip(range(1, 13), list(_ff3(range(1, 13)).values()), strict=True)})

    ctp = calendar_time_portfolio(events, k_months=3)
    month_ci = stationary_block_bootstrap_alpha(ctp, ff3, 3)
    event_ci = event_bootstrap_alpha(events, ff3, 3)

    assert month_ci is not None and event_ci is not None
    month_w = month_ci[1] - month_ci[0]
    event_w = event_ci[1] - event_ci[0]
    assert event_w > 3 * month_w, (
        f"event-level CI ({event_w:.5f}) must be materially wider than the "
        f"month-resampled CI ({month_w:.5f}) on a dispersed cohort")
