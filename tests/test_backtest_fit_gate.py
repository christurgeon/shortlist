from shortlist.backtest.fit import FitResult
from shortlist.backtest.report import evaluate_gate


def _passing_result():
    # 5 folds, all positive paired diffs ~0.05, tight -> t-stat well above 2,
    # n_periods >= 36, edge >= 0.02.
    diffs = [0.05, 0.045, 0.055, 0.05, 0.048]
    return FitResult(weights={"quality": 0.25, "moat": 0.25, "growth": 0.25, "value": 0.25},
                     oos_ic=0.10, in_sample_ic=0.14, n_periods=40,
                     fitted_weights={"quality": 0.2, "moat": 0.2, "growth": 0.3, "value": 0.3},
                     prior_oos_ic=0.05, shrunk_oos_ic=0.10, n_oos_folds=5,
                     fold_diffs=diffs)


def test_gate_endorses_when_all_conditions_clear():
    v = evaluate_gate(_passing_result())
    assert v.endorsed is True


def test_gate_blocks_on_too_few_periods():
    import dataclasses
    r = dataclasses.replace(_passing_result(), n_periods=30)
    v = evaluate_gate(r)
    assert v.endorsed is False and "n_periods" in v.reason


def test_gate_blocks_on_too_few_folds():
    import dataclasses
    r = dataclasses.replace(_passing_result(), n_oos_folds=4, fold_diffs=[0.05] * 4)
    v = evaluate_gate(r)
    assert v.endorsed is False and "n_oos_folds" in v.reason


def test_gate_blocks_on_small_edge():
    import dataclasses
    r = dataclasses.replace(_passing_result(), fold_diffs=[0.005, 0.004, 0.006, 0.005, 0.005])
    v = evaluate_gate(r)
    assert v.endorsed is False and "edge" in v.reason.lower()


def test_gate_blocks_on_fold_disagreement():
    import dataclasses
    # mean still >= 0.02 but two folds negative -> only 3/5 positive
    r = dataclasses.replace(_passing_result(), fold_diffs=[0.13, 0.12, -0.02, -0.02, 0.05])
    v = evaluate_gate(r)
    assert v.endorsed is False and "agreement" in v.reason.lower()


def test_gate_blocks_on_low_tstat():
    import dataclasses
    # mean >= 0.02, all positive, but high variance -> t-stat < 2
    r = dataclasses.replace(_passing_result(), fold_diffs=[0.001, 0.20, 0.001, 0.18, 0.002])
    v = evaluate_gate(r)
    assert v.endorsed is False and "t-stat" in v.reason.lower()
