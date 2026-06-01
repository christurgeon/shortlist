import pytest

from shortlist.backtest.fit import fit_weights, FitGuardError

PRIOR = {"quality": 0.2, "moat": 0.2, "growth": 0.15, "opportunity": 0.3, "insider": 0.15}


def _planted(n_periods=40):
    # 'opportunity' perfectly predicts return; others are noise. The fitter should
    # tilt weight toward opportunity vs the prior.
    rows = []
    for p in range(n_periods):
        for k in range(30):
            sub = {"quality": float((k * 7) % 100), "moat": float((k * 13) % 100),
                   "growth": float((k * 3) % 100), "opportunity": float(k),
                   "insider": float((k * 11) % 100)}
            fwd = float(k)                      # return tracks opportunity exactly
            rows.append((p, sub, fwd))
    return rows


def test_fit_refuses_below_floor():
    with pytest.raises(FitGuardError):
        fit_weights(_planted(n_periods=5), PRIOR, min_periods=24)


def test_fit_recovers_signal_and_shrinks_to_prior():
    res = fit_weights(_planted(40), PRIOR, min_periods=24, shrink=0.5)
    assert res.weights["opportunity"] > PRIOR["opportunity"]
    assert abs(sum(res.weights.values()) - 1.0) < 1e-6
    assert res.oos_ic is not None
    assert all(w > 0 for w in res.weights.values())
