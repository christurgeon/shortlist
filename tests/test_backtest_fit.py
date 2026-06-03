import pytest

from shortlist.backtest.fit import fit_weights, FitGuardError

PRIOR = {"quality": 0.2, "moat": 0.2, "growth": 0.15, "value": 0.22,
         "momentum": 0.08, "insider": 0.15}


def _planted(n_periods=40):
    # 'value' perfectly predicts return; others are noise. The fitter should
    # tilt weight toward value vs the prior.
    rows = []
    for p in range(n_periods):
        for k in range(30):
            sub = {"quality": float((k * 7) % 100), "moat": float((k * 13) % 100),
                   "growth": float((k * 3) % 100), "value": float(k),
                   "momentum": float((k * 17) % 100),
                   "insider": float((k * 11) % 100)}
            fwd = float(k)                      # return tracks value exactly
            rows.append((p, sub, fwd))
    return rows


def test_fit_refuses_below_floor():
    with pytest.raises(FitGuardError):
        fit_weights(_planted(n_periods=5), PRIOR, min_periods=24)


def test_fit_recovers_signal_and_shrinks_to_prior():
    res = fit_weights(_planted(40), PRIOR, min_periods=24, shrink=0.5)
    assert res.weights["value"] > PRIOR["value"]
    assert abs(sum(res.weights.values()) - 1.0) < 1e-6
    assert res.oos_ic is not None
    assert all(w > 0 for w in res.weights.values())


from statistics import mean


def test_fit_populates_paired_oos_fields():
    res = fit_weights(_planted(40), PRIOR, min_periods=24, shrink=0.5, n_folds=4)
    # value perfectly predicts -> fitting beats prior out-of-sample
    assert res.prior_oos_ic is not None
    assert res.shrunk_oos_ic is not None
    assert res.shrunk_oos_ic > res.prior_oos_ic
    assert res.n_oos_folds >= 1
    assert len(res.fold_diffs) == res.n_oos_folds
    assert mean(res.fold_diffs) > 0
    # pre-shrink fitted is surfaced and tilts toward value vs the prior
    assert res.fitted_weights["value"] > res.weights["value"]


def _two_name_periods(n_periods=24):
    # every period has only 2 names -> spearman needs >=3 pairs -> IC is None
    # for every fold, so n_oos_folds must collapse to 0 (not n_folds-1).
    rows = []
    for p in range(n_periods):
        for k in range(2):
            sub = {"quality": float(k), "moat": float(k), "growth": float(k),
                   "value": float(k), "momentum": float(k), "insider": float(k)}
            rows.append((p, sub, float(k)))
    return rows


def test_n_oos_folds_counts_realized_folds_not_nfolds_minus_one():
    res = fit_weights(_two_name_periods(24), PRIOR, min_periods=24, n_folds=4)
    assert res.n_oos_folds == 0          # all per-fold ICs None -> none realized
    assert res.fold_diffs == []


def test_min_period_gap_days_guards_dense_date_periods():
    from datetime import date
    from shortlist.backtest.fit import FitGuardError
    rows = []
    base = date(2024, 1, 1).toordinal()
    for d in range(24):
        # consecutive calendar days -> 1-day spacing, far below a 90-day gap
        pid = date.fromordinal(base + d)
        for k in range(30):
            sub = {"quality": float(k), "value": float(k), "moat": float(k),
                   "growth": float(k), "momentum": float(k), "insider": float(k)}
            rows.append((pid, sub, float(k)))
    with pytest.raises(FitGuardError):
        fit_weights(rows, PRIOR, min_periods=24, min_period_gap_days=90)
