from datetime import date

from shortlist.scout.validate import CohortMeasurement, MeasuredEvent, decide

_PREREG = {"k_months": 12, "min_measurable_frac": 0.90, "min_independent_blocks": 2}


def _measurement(frac_meas=1.0, n=30, vintage_years=None):
    n_meas = int(round(frac_meas * n))
    if vintage_years is None:
        evs = [MeasuredEvent("s", f"T{i}", date(2025, 1, 1), 0.0, i < n_meas, 0.5, False, 60.0)
               for i in range(n)]
    else:
        evs = []
        for i in range(n):
            yr = vintage_years[i] if i < len(vintage_years) else 2025
            evs.append(MeasuredEvent("s", f"T{i}", date(yr, 1, 1), 0.0, i < n_meas, 0.5, False, 60.0))
    return CohortMeasurement("s", n, n_meas, evs)


def test_insufficient_when_measurable_fraction_below_floor():
    v = decide(_measurement(0.5), ctp_rows=[], ff3={}, k_months=12, prereg=_PREREG)
    assert v.verdict == "INSUFFICIENT"
    assert "measurable" in " ".join(v.notes).lower()


def test_insufficient_when_too_few_independent_blocks():
    # 12 months of CTP, K=12 -> 1 independent block < 2 required
    ff3 = {f"2025-{m:02d}": (0.0, 0.0, 0.0, 0.0) for m in range(1, 13)}
    ctp = [(mo, 0.01, 1) for mo in ff3]
    v = decide(_measurement(1.0), ctp, ff3, k_months=12, prereg=_PREREG)
    assert v.verdict == "INSUFFICIENT"


def _ff3_varying(years):
    # NON-ZERO variance on all three factors (as in test_scout_validate_stats.py's _ff3
    # helper) so X'X is well-conditioned -- an all-zero-factor fixture makes the FF3 design
    # matrix exactly singular and ols() correctly raises/abstains (None), which would make
    # these tests spuriously pass/fail on an INSUFFICIENT-by-abstention path instead of
    # actually exercising the KILL branch.
    return {
        f"{y}-{m:02d}": (0.01 * ((m % 3) - 1), 0.001 * ((m % 2) - 1), 0.002 * ((m % 4) - 1.5), 0.003)
        for y in years for m in range(1, 13)
    }


def test_kill_when_alpha_ci_entirely_negative():
    # 48 months, K=12 -> 4 blocks; constant -2%/month excess (zero factor loading) -> CI < 0
    ff3 = _ff3_varying((2022, 2023, 2024, 2025))
    ctp = [(mo, rf - 0.02, 1) for mo, (mkt, smb, hml, rf) in ff3.items()]
    v = decide(_measurement(1.0, 60), ctp, ff3, k_months=12, prereg=_PREREG)
    assert v.verdict == "KILL"


def test_never_promotes_even_with_strong_positive_alpha():
    ff3 = _ff3_varying((2022, 2023, 2024, 2025))
    ctp = [(mo, rf + 0.05, 1) for mo, (mkt, smb, hml, rf) in ff3.items()]
    v = decide(_measurement(1.0, 60), ctp, ff3, k_months=12, prereg=_PREREG)
    assert v.verdict in {"HOLD", "INSUFFICIENT"}     # PROMOTE is not a possible output
    assert v.verdict != "PROMOTE"


# --- R-A4: vintage-stratified floor -----------------------------------------------------

def test_insufficient_when_a_vintage_bucket_below_floor_despite_pooled_pass():
    # Pooled: 100 events, 92 measurable -> 0.92 >= 0.90 floor (pooled PASSES).
    # But the 2024 vintage bucket (10 events, only 4 measurable = 0.40) has >= min_bucket_events (5)
    # and falls well below the floor -> INSUFFICIENT despite the pooled pass.
    n = 100
    n_meas = 92
    evs = []
    # 10 events in 2024, only 4 measurable
    for i in range(10):
        evs.append(MeasuredEvent("s", f"OLD{i}", date(2024, 1, 1), 0.0, i < 4, 0.5, False, 60.0))
    # remaining 90 events in 2025, 88 measurable (keeps pooled frac at 92/100)
    for i in range(90):
        evs.append(MeasuredEvent("s", f"NEW{i}", date(2025, 1, 1), 0.0, i < 88, 0.5, False, 60.0))
    measurement = CohortMeasurement("s", n, n_meas, evs)
    assert measurement.measurable_fraction() >= 0.90     # sanity: pooled floor passes

    ff3 = {f"{y}-{m:02d}": (0.0, 0.0, 0.0, 0.0) for y in (2022, 2023, 2024, 2025) for m in range(1, 13)}
    ctp = [(mo, 0.01, 1) for mo in ff3]
    v = decide(measurement, ctp, ff3, k_months=12, prereg=_PREREG)
    assert v.verdict == "INSUFFICIENT"
    assert "vintage" in " ".join(v.notes).lower() or "2024" in " ".join(v.notes)


def test_vintage_floor_ignores_buckets_below_min_bucket_events():
    # A 2024 bucket with only 3 events (< min_bucket_events=5) and 0 measurable should NOT
    # trip the vintage floor -- too few events in that bucket to be statistically meaningful.
    n = 100
    n_meas = 90
    evs = []
    for i in range(3):
        evs.append(MeasuredEvent("s", f"OLD{i}", date(2024, 1, 1), 0.0, False, 0.5, False, 60.0))
    for i in range(97):
        evs.append(MeasuredEvent("s", f"NEW{i}", date(2025, 1, 1), 0.0, i < 90, 0.5, False, 60.0))
    measurement = CohortMeasurement("s", n, n_meas, evs)

    ff3 = {f"2025-{m:02d}": (0.0, 0.0, 0.0, 0.0) for m in range(1, 13)}
    ff3.update({f"2024-{m:02d}": (0.0, 0.0, 0.0, 0.0) for m in range(1, 13)})
    ctp = [(mo, 0.01, 1) for mo in ff3]
    prereg = dict(_PREREG, min_independent_blocks=1)
    v = decide(measurement, ctp, ff3, k_months=12, prereg=prereg)
    assert v.verdict != "INSUFFICIENT" or "vintage" not in " ".join(v.notes).lower()


# --- R-B5: contested-prior (raw cohort) framing on KILL ---------------------------------

def test_kill_note_flags_raw_cohort_as_confirmatory_not_new_evidence():
    ff3 = _ff3_varying((2022, 2023, 2024, 2025))
    ctp = [(mo, rf - 0.02, 1) for mo, (mkt, smb, hml, rf) in ff3.items()]
    v = decide(_measurement(1.0, 60), ctp, ff3, k_months=12, prereg=_PREREG, cohort_type="raw")
    assert v.verdict == "KILL"
    assert v.cohort_type == "raw"
    joined = " ".join(v.notes).lower()
    assert "confirmatory" in joined
    assert "not new evidence" in joined or "not additional evidence" in joined


def test_kill_note_omits_raw_caveat_for_scored_gated_cohort():
    ff3 = _ff3_varying((2022, 2023, 2024, 2025))
    ctp = [(mo, rf - 0.02, 1) for mo, (mkt, smb, hml, rf) in ff3.items()]
    v = decide(_measurement(1.0, 60), ctp, ff3, k_months=12, prereg=_PREREG, cohort_type="scored_gated")
    assert v.verdict == "KILL"
    assert v.cohort_type == "scored_gated"
    joined = " ".join(v.notes).lower()
    assert "confirmatory" not in joined
