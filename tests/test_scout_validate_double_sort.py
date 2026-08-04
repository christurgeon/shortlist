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
        # Composites are JITTERED rather than a flat 80/20. A two-point composite is
        # degenerate under a re-splitting bootstrap: the replicate median lands ON the tied
        # value about half the time, which sends every drawn event to the HIGH side and
        # empties LOW, so ~51% of replicates are discarded and the CI correctly abstains
        # (measured: 102/200 discarded). That abstention is right for a genuinely degenerate
        # cohort but it is an artifact of the fixture, not of the signal being tested here --
        # real cohorts carry 0.4-1.2% ties at the median. The jitter keeps every HIGH strictly
        # above every LOW, so the median split and both bucket sizes are unchanged.
        events.append(MeasuredEvent("s", f"H{i}", d, hi_ret, True, 0.9, False, 80.0 + i * 0.1))
        events.append(MeasuredEvent("s", f"L{i}", d, lo_ret, True, 0.9, False, 20.0 + i * 0.1))
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


def test_double_sort_excludes_months_where_only_one_side_holds():
    # HIGH fires every month Jan..Jul (i=0..6); LOW fires only Feb..Jul (i=1..6) -> Jan is a
    # high-only month with no LOW counterpart. common_months = set(hi) & set(lo) must drop
    # Jan, leaving 6 (not 7) spread rows. K=1 so holdings never smear across months.
    #
    # Stronger control: rebuild the cohort with the Jan HIGH event simply absent. Because
    # k_months=1 means each month's CTP row depends only on events dated THAT month (no
    # cross-month bleed), and because the median split lands on the same HIGH/LOW membership
    # for the surviving Feb..Jul events in both cohorts (checked below via n_high/n_low), the
    # Feb..Jul spread rows must be byte-identical whether or not the Jan event exists -> the
    # excluded month contributed nothing to the spread stats.
    def _hi(i, composite):
        y, m = _month(i)
        d = date(y, m, 15)
        ret = 0.05 + 0.002 * ((i % 3) - 1)
        return MeasuredEvent("s", f"H{i}", d, ret, True, 0.9, False, composite)

    def _lo(i, composite):
        y, m = _month(i)
        d = date(y, m, 15)
        ret = -0.02 + 0.001 * ((i % 4) - 1.5)
        return MeasuredEvent("s", f"L{i}", d, ret, True, 0.9, False, composite)

    ff3 = _ff3_for_months(7)

    # Full cohort: HIGH months 0..6 (composites 50..56), LOW months 1..6 (composites 10..15).
    hi_full = [_hi(i, 50 + i) for i in range(7)]
    lo_full = [_lo(i, 10 + (i - 1)) for i in range(1, 7)]
    full_result = double_sort(hi_full + lo_full, k_months=1, ff3=ff3, min_bucket_events=1,
                              min_independent_blocks=1, n_boot=200)

    # Control cohort: identical except the Jan (i=0) HIGH event is simply absent.
    hi_ctrl = [_hi(i, 50 + i) for i in range(1, 7)]
    lo_ctrl = [_lo(i, 10 + (i - 1)) for i in range(1, 7)]
    control_result = double_sort(hi_ctrl + lo_ctrl, k_months=1, ff3=ff3, min_bucket_events=1,
                                 min_independent_blocks=1, n_boot=200)

    assert full_result is not None and control_result is not None

    # Median split sanity: full cohort keeps all 7 HIGH events (composite >= median 50) and
    # all 6 LOW events; control cohort has 6 HIGH events (the Jan one is gone) and 6 LOW.
    assert full_result["n_high"] == 7
    assert full_result["n_low"] == 6
    assert control_result["n_high"] == 6
    assert control_result["n_low"] == 6

    # The intersection excludes Jan: 6 common months, strictly fewer than HIGH's own 7 months.
    assert full_result["months"] == 6
    assert full_result["months"] < full_result["n_high"]

    # The excluded high-only month contributed NOTHING to the spread: with it entirely absent
    # from the cohort, every POINT-ESTIMATE spread statistic is unchanged.
    assert full_result["months"] == control_result["months"]
    assert full_result["effective_blocks"] == control_result["effective_blocks"]
    assert full_result["spread_alpha_monthly"] == control_result["spread_alpha_monthly"]

    # This test used to also assert `spread_ci` equality. That held only because the CI came
    # from resampling MONTHS of an already-common-months-only series, so a 13-event and a
    # 12-event cohort bootstrapped literally the same row sequence. The CI now resamples
    # ISSUERS, under which these are genuinely different populations and the intervals are
    # not required to match -- the equality was a property of the old estimator, not of the
    # invariant this test exists to pin. Asserting it again would be vacuous here anyway:
    # a 7-month cohort is below the bootstrap's `min_obs`, so BOTH sides abstain and
    # `None == None` would pass without testing anything. Pin the abstention EXPLICITLY
    # instead, so a thin cohort can never silently ship a number from a different model.
    for r in (full_result, control_result):
        assert r["spread_ci"] is None
        assert r["spread_ci_method"] == "unavailable"


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


def test_signal_verdict_double_sort_field_is_optional_and_positionally_stable():
    # additive: constructing a verdict without double_sort still works, and asdict() carries
    # the new key with a None default so existing consumers tolerate it. `double_sort` is no
    # longer the LAST field -- v2 design B2 appends `n_immature`/`n_events` after it (both
    # defaulted, back-compat) -- so this pins double_sort's continued presence + default,
    # not its position.
    from dataclasses import asdict, fields

    v = SignalVerdict(
        signal="s", verdict="HOLD", ir=None, alpha_monthly=None, alpha_ci=None,
        effective_blocks=0, n_selected=0, n_measurable=0, measurable_fraction=0.0,
        sensitivity_flip=False,
    )
    assert v.double_sort is None
    d = asdict(v)
    assert d["double_sort"] is None
    names = [f.name for f in fields(SignalVerdict)]
    assert names.index("double_sort") == names.index("n_immature") - 1
    # Every later field is APPENDED (never inserted), so earlier positional slots survive:
    # n_immature/n_events keep their order and slots, and `alpha_suppressed` (R-0f level
    # suppression) sits after them at the very end.
    assert names[-3:] == ["n_immature", "n_events", "alpha_suppressed"]
    assert v.alpha_suppressed is False


# --- R-0f: the double-sort's ABSOLUTE legs don't get the spread's bias-cancellation ------

def _ds(**over):
    base = {"n_high": 20, "n_low": 22, "months": 30, "effective_blocks": 6,
            "spread_alpha_monthly": 0.0244, "spread_ci": (0.001, 0.048),
            "high_ir": 1.0438, "low_ir": 0.4956}
    base.update(over)
    return base


def _verdict(**over):
    base = dict(signal="s", verdict="INSUFFICIENT", ir=None, alpha_monthly=None,
                alpha_ci=None, effective_blocks=6, n_selected=100, n_measurable=92,
                measurable_fraction=0.92, sensitivity_flip=False,
                cohort_type="scored_gated")
    base.update(over)
    return SignalVerdict(**base)


def test_attach_double_sort_blanks_absolute_legs_when_the_level_is_suppressed():
    """high_ir/low_ir are per-bucket LEVELS, not differences -- they carry exactly the
    attrition bias the measurability floor rejected, so a suppressed verdict must not ship
    them one key down from the fields R-0f just blanked (the raw --json / persisted
    artifact surface). Reproduces the real shape in scout/validate-latest.json, where an
    edgar:activist_13d scored cohort failed its 2025 vintage bucket while carrying
    high_ir 1.04."""
    from shortlist.scout.validate import attach_double_sort

    v = attach_double_sort(_verdict(alpha_suppressed=True), _ds())

    assert v.double_sort["high_ir"] is None
    assert v.double_sort["low_ir"] is None


def test_attach_double_sort_keeps_the_spread_when_the_level_is_suppressed():
    """The spread IS a difference between two identically-measured buckets, so the common
    bias cancels -- suppression must not take it, or the one statistic this data supports
    disappears with the one it doesn't."""
    from shortlist.scout.validate import attach_double_sort

    v = attach_double_sort(_verdict(alpha_suppressed=True), _ds())

    assert v.double_sort["spread_alpha_monthly"] == 0.0244
    assert v.double_sort["spread_ci"] == (0.001, 0.048)
    assert (v.double_sort["n_high"], v.double_sort["n_low"]) == (20, 22)
    assert v.double_sort["effective_blocks"] == 6


def test_attach_double_sort_is_a_passthrough_when_not_suppressed():
    from shortlist.scout.validate import attach_double_sort

    ds = _ds()
    v = attach_double_sort(_verdict(verdict="HOLD"), ds)

    assert v.double_sort == ds
    assert v.double_sort["high_ir"] == 1.0438
    assert ds["high_ir"] == 1.0438          # never mutates the caller's dict


def test_attach_double_sort_tolerates_none():
    from shortlist.scout.validate import attach_double_sort

    assert attach_double_sort(_verdict(alpha_suppressed=True), None).double_sort is None


# --- Issuer-clustered bootstrap + ds floor check (docs/EVALUATOR_CORRECTNESS.md §2-§3) ----

def _multi_event_issuer_cohort(n_months, events_per_issuer=3):
    """Cohort where each ISSUER fires several times — the structure the real cohorts have
    (48-57% of events sit on a multi-event issuer) and the one that distinguishes an
    issuer-clustered resample from an i.i.d.-event one."""
    events = []
    for i in range(n_months):
        y, m = _month(i)
        d = date(y, m, 15)
        for r in range(events_per_issuer):
            events.append(MeasuredEvent("s", f"H{i % 7}", d, 0.05 + 0.002 * ((i + r) % 3 - 1),
                                        True, 0.9, False, 80.0 + i * 0.1 + r * 0.01))
            events.append(MeasuredEvent("s", f"L{i % 7}", d, -0.02 + 0.001 * ((i + r) % 4 - 1.5),
                                        True, 0.9, False, 20.0 + i * 0.1 + r * 0.01))
    return events


def test_replicates_keep_same_issuer_dedup_active():
    """The estimator being bootstrapped must be the estimator being reported.

    The old resample relabelled every drawn event with a unique DRAW index, which did not
    merely un-dedup repeat draws — it disabled calendar_time_portfolio's same-ticker dedup for
    genuinely distinct events of the SAME issuer (measured held-set inflation +19.6% on 13d,
    +23.7% on 8k-neg). Relabelling per ISSUER-COPY keeps dedup active inside a copy.
    """
    from shortlist.scout.validate import _resample_by_issuer

    live = _multi_event_issuer_cohort(12)
    def rand():
        return 0.0                                               # always draw issuer 0

    draw = _resample_by_issuer(live, rand)
    # Every drawn event belongs to one issuer, drawn n_issuers times -> exactly n_issuers
    # distinct labels, NOT one label per event (which is what per-draw relabelling gave).
    n_issuers = len({m.ticker for m in live})
    assert len({m.ticker for m in draw}) == n_issuers
    assert len(draw) > n_issuers          # each copy carries ALL that issuer's events


def test_spread_ci_abstains_rather_than_falling_back_to_the_month_bootstrap():
    """A thin cohort must yield no CI at all, never a month-bootstrap number wearing the
    event bootstrap's label. The month bootstrap is most artificially tight exactly on thin
    cohorts, so falling back would be anti-conservative where the data is weakest."""
    thin = _ranked_cohort(7)
    result = double_sort(thin, k_months=1, ff3=_ff3_for_months(7), min_bucket_events=1,
                         min_independent_blocks=1, n_boot=100)
    assert result is not None
    assert result["spread_ci"] is None
    assert result["spread_ci_method"] == "unavailable"


def test_spread_ci_is_stable_across_seeds():
    """A same-seed-same-output check would only prove an LCG is an LCG. What matters is that
    the Monte-Carlo error is small enough that the reported endpoints mean something."""
    measured = _ranked_cohort(30)
    ff3 = _ff3_for_months(30)
    cis = [double_sort(measured, k_months=1, ff3=ff3, min_bucket_events=10,
                       min_independent_blocks=5, n_boot=300, seed=s)["spread_ci"]
           for s in (12345, 777, 999)]
    assert all(c is not None for c in cis)
    width = sum(c[1] - c[0] for c in cis) / 3.0
    for lo, hi in cis:
        assert abs(lo - cis[0][0]) < 0.25 * width
        assert abs(hi - cis[0][1]) < 0.25 * width


def test_per_bucket_fractions_are_not_tautologically_one():
    """`eligible` is already filtered on `measurable`, so splitting THAT reports 1.0/1.0 and
    the disclosure is worthless. The fractions must be computed over ALL composite-defined
    events, including the non-measurable ones."""
    measured = _ranked_cohort(30)
    # Add composite-defined but NON-measurable events, all on the LOW side.
    for i in range(20):
        y, m = _month(i)
        measured.append(MeasuredEvent("s", f"X{i}", date(y, m, 15), None, False, 0.9,
                                      False, 20.0 + i * 0.1))
    result = double_sort(measured, k_months=1, ff3=_ff3_for_months(30), min_bucket_events=10,
                         min_independent_blocks=5, n_boot=100)
    assert result["high_frac"] == 1.0                     # no unmeasurable events up here
    assert result["low_frac"] < 1.0                       # ... but plenty down there
    assert result["high_frac"] > result["low_frac"]       # the asymmetry is now visible


def test_ds_floor_failure_blanks_absolute_legs_but_never_the_spread():
    """TODO 0g: the ds cohort is a different population from the one decide() floored, so it
    can fail a floor its parent passes. The SPREAD survives (it is a difference between two
    buckets measured the same way); the ABSOLUTE per-bucket legs do not."""
    from shortlist.scout.validate import attach_double_sort

    ds = _ds()
    v = attach_double_sort(_verdict(verdict="HOLD"), ds, ds_floor_failed=True)
    assert v.alpha_suppressed is False                     # the PARENT still cleared its floor
    assert v.double_sort["high_ir"] is None
    assert v.double_sort["low_ir"] is None
    assert v.double_sort["level_suppressed"] is True
    assert v.double_sort["spread_alpha_monthly"] == ds["spread_alpha_monthly"]
    assert v.double_sort["spread_ci"] == ds["spread_ci"]
    assert any("double-sort" in n and "SUPPRESSED" in n for n in v.notes)


# --- per-bucket floor + adjudication state (docs/EVALUATOR_GUARDS.md §3, §4) --------------

def _cohort_with_unmeasurable_low(n_months=30, n_bad=25):
    """Well-measured HIGH bucket, badly-measured LOW bucket — the asymmetry under which
    'attrition cancels between two identically-measured buckets' stops holding."""
    measured = _ranked_cohort(n_months)
    for i in range(n_bad):
        y, m = _month(i % n_months)
        measured.append(MeasuredEvent("s", f"X{i}", date(y, m, 15), None, False, 0.9,
                                      False, 20.0 + i * 0.01))
    return measured


def test_a_bucket_below_the_floor_suppresses_the_SPREAD_not_the_fractions():
    """The fix that replaced the first draft. The spread's claim to survive cohort-level
    suppression is that it differences two identically-measured buckets; when one bucket is
    below the registered floor that premise is untestable, so the SPREAD stops being quotable.
    The fractions are NOT suppressed — they are the measurement of the problem, not a
    statistic biased by it."""
    r = double_sort(_cohort_with_unmeasurable_low(), k_months=1, ff3=_ff3_for_months(30),
                    min_bucket_events=10, min_independent_blocks=5, n_boot=100,
                    min_measurable_frac=0.90)
    assert r["bucket_below_floor"] is True
    assert r["level_suppressed"] is True
    assert r["spread_alpha_monthly"] is None
    assert r["spread_ci"] is None
    assert r["spread_ci_method"] == "suppressed_bucket_floor"
    # the diagnostic survives, and shows WHY
    assert r["high_frac"] == 1.0
    assert r["low_frac"] < 0.90
    assert r["n_high_pool"] > 0 and r["n_low_pool"] > 0


def test_both_buckets_above_the_floor_leaves_the_spread_quotable():
    r = double_sort(_ranked_cohort(30), k_months=1, ff3=_ff3_for_months(30),
                    min_bucket_events=10, min_independent_blocks=5, n_boot=100,
                    min_measurable_frac=0.90)
    assert r["bucket_below_floor"] is False
    assert r["level_suppressed"] is False
    assert r["spread_alpha_monthly"] is not None


def test_unadjudicated_result_does_not_claim_to_be_cleared():
    """`level_suppressed` used to be hard-coded False, so a caller that never reached
    `attach_double_sort` got a dict asserting a decision nobody had made — which is how an
    ad-hoc replay script produced 'cleared'-looking numbers off a rejected cohort. Absent an
    adjudication input, the field must read None, not False."""
    r = double_sort(_ranked_cohort(30), k_months=1, ff3=_ff3_for_months(30),
                    min_bucket_events=10, min_independent_blocks=5, n_boot=100)
    assert r["level_suppressed"] is None
    assert "bucket_below_floor" not in r


def test_per_bucket_fractions_are_mature_only_like_the_floor_they_are_tested_against():
    """`measurable_fraction()` divides by a MATURE-ONLY denominator (the H2 fix). If the
    per-bucket fractions counted immature events they would be old-style pooled numbers,
    incomparable to the floor, the digest's pooled fraction, and the vintage buckets — the
    trap `backfill.py`'s `fraction_note` already exists to prevent."""
    measured = _ranked_cohort(30)
    for i in range(20):                      # immature: not yet resolvable, not a data gap
        y, m = _month(i)
        ev = MeasuredEvent("s", f"I{i}", date(y, m, 15), None, False, 0.9, False,
                           80.0 + i * 0.01)
        ev.immature = True
        measured.append(ev)
    r = double_sort(measured, k_months=1, ff3=_ff3_for_months(30), min_bucket_events=10,
                    min_independent_blocks=5, n_boot=100)
    assert r["high_frac"] == 1.0             # immature events excluded, not counted as lost
    assert r["n_high_pool"] == 30
