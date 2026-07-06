from datetime import date

from shortlist.backtest.prices import PriceHistory
from shortlist.scout.validate import CohortMeasurement, MeasuredEvent, decide, measure_cohort

_PREREG = {"k_months": 12, "min_measurable_frac": 0.90, "min_independent_blocks": 2}


def _hist(ticker, pairs):
    dates = [d for d, _ in pairs]
    closes = [c for _, c in pairs]
    return PriceHistory(ticker, dates, closes, nominal_closes=list(closes))


def _ev(ticker, d, **kw):
    base = dict(signal="s", ticker=ticker, cik=None, event_date=d, as_of_price=None,
                strength=0.9, gated=False, composite=60.0, origin="live", meta={})
    base.update(kw)
    return base


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
    # sanity: measurable_fraction() divides the STORED n_selected/n_measurable ints as
    # given (this CohortMeasurement is hand-built, not run through measure_cohort's H2
    # mature-only factory, and has zero immature events among its MeasuredEvents) -- so
    # this hand-built fraction passes the floor regardless of the H2 fix.
    assert measurement.measurable_fraction() >= 0.90

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


# --- verdict honesty: alpha uncomputable is INSUFFICIENT, not HOLD ----------------------

def test_insufficient_when_alpha_uncomputable_despite_passing_sample_gates():
    # 48 CTP months (>=2 blocks at K=12) and full measurable fraction pass the sample gates,
    # but an EMPTY ff3 (e.g. a failed factor fetch) means ff3_alpha -> (None, []) and the
    # bootstrap -> None. That must read as "could not compute alpha" -> INSUFFICIENT, never
    # the "no negative evidence; HOLD" fall-through.
    ctp = [(f"{y}-{m:02d}", 0.01, 1) for y in (2022, 2023, 2024, 2025) for m in range(1, 13)]
    v = decide(_measurement(1.0, 60), ctp, ff3={}, k_months=12, prereg=_PREREG)
    assert v.verdict == "INSUFFICIENT"
    assert v.verdict != "HOLD"
    assert "alpha" in " ".join(v.notes).lower()


# --- enforce the pre-registered factor model --------------------------------------------

def test_insufficient_when_prereg_factor_model_unsupported():
    ff3 = _ff3_varying((2022, 2023, 2024, 2025))
    ctp = [(mo, rf + 0.02, 1) for mo, (mkt, smb, hml, rf) in ff3.items()]
    prereg = dict(_PREREG, factor_model="ff5")
    v = decide(_measurement(1.0, 60), ctp, ff3, k_months=12, prereg=prereg)
    assert v.verdict == "INSUFFICIENT"
    joined = " ".join(v.notes).lower()
    assert "factor_model" in joined and "ff5" in joined


def test_default_factor_model_ff3_still_runs():
    # No factor_model key -> defaults to ff3, so the guard is a no-op (regression guard).
    ff3 = _ff3_varying((2022, 2023, 2024, 2025))
    ctp = [(mo, rf + 0.05, 1) for mo, (mkt, smb, hml, rf) in ff3.items()]
    prereg = {"k_months": 12, "min_measurable_frac": 0.90, "min_independent_blocks": 2}
    v = decide(_measurement(1.0, 60), ctp, ff3, k_months=12, prereg=prereg)
    assert v.verdict in {"KILL", "HOLD"}     # ran the real FF3 path, not the guard
    assert "factor_model" not in " ".join(v.notes).lower()


# --- I2: the immature-exclusion asymmetry (design v2 resolution 4) ----------------------

def test_i2_synthetic_asymmetry_immature_exclusion_lifts_one_cohort_not_the_other():
    """Deterministic regression for the I2 asymmetry -- replaces the earlier
    manual-production-rerun acceptance item. Two cohorts of the same raw size:

    Cohort A: 80 mature events, ALL genuinely measurable (real entry+exit price data),
    plus 20 immature (recent, entry price only, horizon still pending). The RAW/pooled
    fraction (denominator = every event, the pre-fix behaviour) is 80/100 = 0.80 < floor;
    the corrected mature-only fraction is 80/80 = 1.0 -- the fix rescues it.

    Cohort B: 100 events, ZERO immature (every target is already in the past), but 30 are
    genuine no-series losses -- real survivorship attrition. Mature-only == raw here
    (nothing to rescue), and it stays 0.70 < floor -- the fix must NOT rescue this one.
    """
    k = 12
    as_of = date(2026, 7, 2)

    evs_a, hists_a = [], {}
    for i in range(80):
        tk = f"MA{i}"
        hists_a[tk] = _hist(tk, [(date(2023, 1, 31), 100.0), (date(2024, 1, 31), 110.0)])
        evs_a.append(_ev(tk, "2023-01-31"))
    for i in range(20):
        tk = f"IMM{i}"
        hists_a[tk] = _hist(tk, [(date(2026, 6, 1), 50.0)])     # entry only, still pending
        evs_a.append(_ev(tk, "2026-06-01"))
    cohort_a = measure_cohort(evs_a, "s", horizon_months=k, hist_by_ticker=hists_a,
                              delisting_return=-0.30, as_of=as_of)
    assert cohort_a.n_immature == 20
    assert cohort_a.n_events == 100
    raw_fraction_a = cohort_a.n_measurable / cohort_a.n_events
    assert raw_fraction_a < 0.90                       # pre-fix pooled fraction fails
    assert cohort_a.measurable_fraction() == 1.0       # corrected mature-only fraction clears

    evs_b, hists_b = [], {}
    for i in range(70):
        tk = f"MB{i}"
        hists_b[tk] = _hist(tk, [(date(2023, 1, 31), 100.0), (date(2024, 1, 31), 110.0)])
        evs_b.append(_ev(tk, "2023-01-31"))
    for i in range(30):
        evs_b.append(_ev(f"GONE{i}", "2023-01-31"))     # no hist at all -> genuine loss
    cohort_b = measure_cohort(evs_b, "s", horizon_months=k, hist_by_ticker=hists_b,
                              delisting_return=None, as_of=as_of)
    assert cohort_b.n_immature == 0
    assert cohort_b.measurable_fraction() < 0.90        # genuine losses -- correction can't help

    ff3 = _ff3_varying((2022, 2023, 2024, 2025))
    ctp_a = [(mo, rf + 0.05, 1) for mo, (mkt, smb, hml, rf) in ff3.items()]
    v_a = decide(cohort_a, ctp_a, ff3, k_months=k, prereg=_PREREG)
    v_b = decide(cohort_b, [], {}, k_months=k, prereg=_PREREG)
    assert v_a.verdict != "INSUFFICIENT"                # cleared the floor; never PROMOTE
    assert v_b.verdict == "INSUFFICIENT"
    assert "measurable" in " ".join(v_b.notes).lower()


# --- I4: all-immature cohort must not crash the fraction --------------------------------

def test_i4_all_immature_cohort_fraction_zero_no_crash_insufficient():
    hists = {}
    events = []
    for i, mo in enumerate((1, 2, 3)):
        tk = f"NEW{i}"
        hists[tk] = _hist(tk, [(date(2026, mo, 1), 50.0)])
        events.append(_ev(tk, f"2026-{mo:02d}-01"))
    m = measure_cohort(events, "s", horizon_months=12, hist_by_ticker=hists,
                       delisting_return=None, as_of=date(2026, 7, 2))
    assert m.n_selected == 0
    assert m.n_immature == 3
    assert m.measurable_fraction() == 0.0               # no ZeroDivisionError
    v = decide(m, ctp_rows=[], ff3={}, k_months=12, prereg=_PREREG)
    assert v.verdict == "INSUFFICIENT"
