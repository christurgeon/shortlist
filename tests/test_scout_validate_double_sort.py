from datetime import date

from shortlist.scout.validate import MeasuredEvent, SignalVerdict, double_sort


def _month(i, start_year=2022, start_month=1):
    total = start_year * 12 + (start_month - 1) + i
    y, m0 = divmod(total, 12)
    return y, m0 + 1


def _ff3_for_months(n_months, start_year=2022, start_month=1):
    """Small deterministic NON-ZERO variance on all three factors (same shape as the
    _ff3/_ff3_varying helpers in test_scout_validate_stats.py / test_scout_validate_verdict.py)
    so X'X is well-conditioned for the FF3 regression."""
    out = {}
    for i in range(n_months):
        y, m = _month(i, start_year, start_month)
        out[f"{y:04d}-{m:02d}"] = (
            0.01 * ((i % 3) - 1), 0.001 * ((i % 2) - 1), 0.002 * ((i % 4) - 1.5), 0.003,
        )
    return out


def _ranked_cohort(n_months):
    """One high-composite + one low-composite event per month, spread over n_months distinct
    months so composite genuinely ranks forward returns: high events consistently beat low
    events, but with enough month-to-month wiggle that neither side's CTP is perfectly
    constant (a perfectly constant regressand has zero residual variance -> te=0 ->
    information_ratio abstains to None, which would defeat the high_ir > low_ir assertion)."""
    events = []
    for i in range(n_months):
        y, m = _month(i)
        d = date(y, m, 15)
        hi_ret = 0.05 + 0.002 * ((i % 3) - 1)
        lo_ret = -0.02 + 0.001 * ((i % 4) - 1.5)
        events.append(MeasuredEvent("s", f"H{i}", d, hi_ret, True, 0.9, False, 80.0))
        events.append(MeasuredEvent("s", f"L{i}", d, lo_ret, True, 0.9, False, 20.0))
    return events


def test_double_sort_positive_spread_when_composite_ranks_returns():
    n_months = 30
    measured = _ranked_cohort(n_months)
    ff3 = _ff3_for_months(n_months)
    result = double_sort(measured, k_months=1, ff3=ff3, min_bucket_events=10,
                         min_independent_blocks=5, n_boot=200)
    assert result is not None
    assert result["n_high"] == n_months
    assert result["n_low"] == n_months
    assert result["effective_blocks"] == n_months        # k_months=1 -> blocks == months
    assert result["months"] == n_months
    assert result["spread_alpha_monthly"] is not None
    assert result["spread_alpha_monthly"] > 0
    assert result["spread_ci"] is not None
    assert result["high_ir"] is not None and result["low_ir"] is not None
    assert result["high_ir"] > result["low_ir"]


def test_double_sort_none_when_blocks_below_gate():
    # Same shape, squeezed to 3 months -> effective_blocks(3, 1) = 3 < 5 required -> None,
    # even though both sides individually clear min_bucket_events.
    n_months = 3
    measured = _ranked_cohort(n_months)
    ff3 = _ff3_for_months(n_months)
    result = double_sort(measured, k_months=1, ff3=ff3, min_bucket_events=1,
                         min_independent_blocks=5, n_boot=200)
    assert result is None


def test_double_sort_none_when_one_sided_after_tie_split():
    # All ten events share the SAME composite -> the median tie rule sends every event to the
    # high side, leaving the low side empty (< min_bucket_events) -> None.
    events = []
    for i in range(10):
        y, m = _month(i)
        events.append(MeasuredEvent("s", f"T{i}", date(y, m, 15), 0.01, True, 0.9, False, 50.0))
    ff3 = _ff3_for_months(10)
    result = double_sort(events, k_months=1, ff3=ff3, min_bucket_events=3,
                         min_independent_blocks=1, n_boot=200)
    assert result is None


def test_double_sort_ties_at_median_go_to_high_side_deterministically():
    # Ten events, all in the SAME month (so both sides' single CTP row overlaps and the
    # gate passes at effective_blocks=1): composites [10,10,10,10,50,50,90,90,90,90].
    # Sorted median = avg(composites[4], composites[5]) = avg(50, 50) = 50 -> the two 50s
    # join the high side (tie -> high), giving a deterministic 6/4 split, not 4/6 or 5/5.
    d = date(2022, 1, 15)
    composites = [10.0, 10.0, 10.0, 10.0, 50.0, 50.0, 90.0, 90.0, 90.0, 90.0]
    events = [MeasuredEvent("s", f"T{i}", d, 0.01 + 0.001 * i, True, 0.9, False, c)
              for i, c in enumerate(composites)]
    ff3 = _ff3_for_months(1)
    result = double_sort(events, k_months=1, ff3=ff3, min_bucket_events=4,
                         min_independent_blocks=1, n_boot=200)
    assert result is not None
    assert result["n_high"] == 6         # four 90s + the two tied 50s
    assert result["n_low"] == 4          # four 10s
    assert result["effective_blocks"] == 1


def test_double_sort_excludes_composite_none_and_non_measurable_events():
    # 4 events with composite=None (excluded), 1 measurable=False/ret=None despite a composite
    # (excluded), and 8 real events split cleanly at the median -> only the 8 eligible events
    # participate in the split.
    d = date(2022, 1, 15)
    events = [
        MeasuredEvent("s", "N1", d, 0.01, True, 0.9, False, None),
        MeasuredEvent("s", "N2", d, 0.01, True, 0.9, False, None),
        MeasuredEvent("s", "N3", d, 0.01, True, 0.9, False, None),
        MeasuredEvent("s", "N4", d, 0.01, True, 0.9, False, None),
        MeasuredEvent("s", "NM", d, None, False, 0.9, False, 70.0),
    ]
    composites = [10.0, 10.0, 20.0, 20.0, 80.0, 80.0, 90.0, 90.0]
    events += [MeasuredEvent("s", f"E{i}", d, 0.01 + 0.001 * i, True, 0.9, False, c)
               for i, c in enumerate(composites)]
    ff3 = _ff3_for_months(1)
    result = double_sort(events, k_months=1, ff3=ff3, min_bucket_events=4,
                         min_independent_blocks=1, n_boot=200)
    assert result is not None
    assert result["n_high"] == 4          # 80, 80, 90, 90
    assert result["n_low"] == 4           # 10, 10, 20, 20
    assert result["n_high"] + result["n_low"] == 8


def test_double_sort_none_when_no_eligible_events():
    ff3 = _ff3_for_months(1)
    result = double_sort([], k_months=1, ff3=ff3, min_bucket_events=1, min_independent_blocks=1)
    assert result is None


def test_double_sort_deterministic_ci_same_seed():
    n_months = 12
    measured = _ranked_cohort(n_months)
    ff3 = _ff3_for_months(n_months)
    r1 = double_sort(measured, k_months=1, ff3=ff3, min_bucket_events=5,
                     min_independent_blocks=3, n_boot=300, seed=999)
    r2 = double_sort(measured, k_months=1, ff3=ff3, min_bucket_events=5,
                     min_independent_blocks=3, n_boot=300, seed=999)
    assert r1 is not None and r2 is not None
    assert r1["spread_ci"] == r2["spread_ci"]


def test_signal_verdict_double_sort_field_is_last_and_optional():
    # additive: constructing a verdict without double_sort still works, and asdict() carries
    # the new key with a None default so existing consumers tolerate it.
    from dataclasses import asdict, fields

    v = SignalVerdict(
        signal="s", verdict="HOLD", ir=None, alpha_monthly=None, alpha_ci=None,
        effective_blocks=0, n_selected=0, n_measurable=0, measurable_fraction=0.0,
        sensitivity_flip=False,
    )
    assert v.double_sort is None
    d = asdict(v)
    assert d["double_sort"] is None
    assert fields(SignalVerdict)[-1].name == "double_sort"
