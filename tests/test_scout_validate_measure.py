from datetime import date

from shortlist.backtest.prices import PriceHistory
from shortlist.scout.validate import measure_cohort


def _hist(ticker, pairs):
    dates = [d for d, _ in pairs]
    closes = [c for _, c in pairs]
    return PriceHistory(ticker, dates, closes, nominal_closes=list(closes))


def _ev(ticker, d, **kw):
    base = dict(signal="edgar:activist_13d", ticker=ticker, cik=None,
                event_date=d, as_of_price=None, strength=0.9, gated=False,
                composite=60.0, origin="live", meta={})
    base.update(kw)
    return base


_AS_OF = date(2026, 7, 2)


def test_fixed_horizon_return_measured_at_event_plus_horizon():
    # entry 2025-01-31 @100, +3m ~2025-04-30 @110 -> +10% (target well before as_of)
    h = _hist("ABC", [(date(2025, 1, 31), 100.0), (date(2025, 4, 30), 110.0)])
    m = measure_cohort([_ev("ABC", "2025-01-31")], "edgar:activist_13d",
                       horizon_months=3, hist_by_ticker={"ABC": h}, delisting_return=-0.30,
                       as_of=_AS_OF)
    assert m.n_selected == 1 and m.n_measurable == 1
    assert abs(m.events[0].ret - 0.10) < 1e-9
    assert m.events[0].measurable is True


def test_immature_event_excluded_not_measured_early():
    # event 2026-06-01, +3m target ~2026-09-01 is AFTER as_of 2026-06-15 -> the horizon has
    # not elapsed, so the outcome is unknown -> non-measurable (calendar rule, no price peek)
    h = _hist("ABC", [(date(2026, 6, 1), 100.0), (date(2026, 6, 13), 105.0)])
    m = measure_cohort([_ev("ABC", "2026-06-01")], "edgar:activist_13d",
                       horizon_months=3, hist_by_ticker={"ABC": h}, delisting_return=-0.30,
                       as_of=date(2026, 6, 15))
    assert m.events[0].ret is None
    assert m.events[0].measurable is False
    assert m.n_measurable == 0


def test_delisting_gets_partial_return_not_dropped():
    # target ~2025-04-30 is <= as_of 2026-07-02 (in the past) yet the series ends 2025-02-10
    # -> a still-listed stock would have traded through the target -> delisting return applied
    h = _hist("DEAD", [(date(2025, 1, 31), 100.0), (date(2025, 2, 10), 40.0)])
    m = measure_cohort([_ev("DEAD", "2025-01-31")], "edgar:activist_13d",
                       horizon_months=3, hist_by_ticker={"DEAD": h}, delisting_return=-0.55,
                       as_of=_AS_OF)
    assert m.events[0].measurable is True
    assert abs(m.events[0].ret - (-0.55)) < 1e-9


def test_no_series_is_non_measurable():
    m = measure_cohort([_ev("GONE", "2025-01-31")], "edgar:activist_13d",
                       horizon_months=3, hist_by_ticker={}, delisting_return=-0.55,
                       as_of=_AS_OF)
    assert m.n_selected == 1 and m.n_measurable == 0
    assert m.events[0].ret is None and m.events[0].measurable is False


def test_measurable_fraction():
    h_ok = _hist("A", [(date(2025, 1, 31), 100.0), (date(2025, 4, 30), 120.0)])
    evs = [_ev("A", "2025-01-31"), _ev("GONE", "2025-01-31")]
    m = measure_cohort(evs, "edgar:activist_13d", horizon_months=3,
                       hist_by_ticker={"A": h_ok}, delisting_return=None, as_of=_AS_OF)
    assert m.n_selected == 2 and m.n_measurable == 1
    assert abs(m.measurable_fraction() - 0.5) < 1e-9


def test_classified_terminal_override_preferred_over_blanket():
    # series terminated (same shape as test_delisting_gets_partial_return_not_dropped) but the
    # event carries a per-event CLASSIFIED delisting return in meta -- the classifier's value
    # (-0.62) must win over the blanket band value (-0.55) by default (use_event_delisting=True).
    h = _hist("DEAD", [(date(2025, 1, 31), 100.0), (date(2025, 2, 10), 40.0)])
    ev = _ev("DEAD", "2025-01-31", meta={"delisting_event_return": -0.62})
    m = measure_cohort([ev], "edgar:activist_13d", horizon_months=3,
                       hist_by_ticker={"DEAD": h}, delisting_return=-0.55, as_of=_AS_OF)
    assert m.events[0].measurable is True
    assert abs(m.events[0].ret - (-0.62)) < 1e-9


def test_use_event_delisting_false_ignores_classified_override():
    # Same fixture, but use_event_delisting=False must IGNORE the per-event value and fall
    # back to the blanket -- this is what the sensitivity band (_delisting_band_flip) relies
    # on to keep varying the classified events, not just the unclassified ones (spec §6.6).
    h = _hist("DEAD", [(date(2025, 1, 31), 100.0), (date(2025, 2, 10), 40.0)])
    ev = _ev("DEAD", "2025-01-31", meta={"delisting_event_return": -0.62})
    m = measure_cohort([ev], "edgar:activist_13d", horizon_months=3,
                       hist_by_ticker={"DEAD": h}, delisting_return=-0.55, as_of=_AS_OF,
                       use_event_delisting=False)
    assert m.events[0].measurable is True
    assert abs(m.events[0].ret - (-0.55)) < 1e-9


def test_live_shaped_event_without_classified_meta_is_inert():
    # A live-shaped event (no delisting_event_return key at all, e.g. meta={} or a live-origin
    # meta dict with other keys) behaves byte-identically to before the override was added --
    # the blanket fallback still applies. This is the inertness pin.
    h = _hist("DEAD", [(date(2025, 1, 31), 100.0), (date(2025, 2, 10), 40.0)])
    ev = _ev("DEAD", "2025-01-31", meta={"some_other_key": "x"})
    m = measure_cohort([ev], "edgar:activist_13d", horizon_months=3,
                       hist_by_ticker={"DEAD": h}, delisting_return=-0.55, as_of=_AS_OF)
    assert m.events[0].measurable is True
    assert abs(m.events[0].ret - (-0.55)) < 1e-9


def test_delisting_band_flip_uses_band_values_not_classified_override():
    """Regression pin for daily.py:_delisting_band_flip's internal `measure_cohort(...,
    use_event_delisting=False)` kwarg (spec §6.6). The sensitivity band's whole point is to
    vary the delisting-return assumption; a per-event CLASSIFIED terminal return (13D
    backfill) must NOT override the band's fixed values, or the band collapses to a single
    (classified) return for every member and can never show a sign disagreement -- silently
    masking the guard `decide()` relies on to downgrade a HOLD to INSUFFICIENT.

    Fixture: one TERMINATED-series event carrying meta={"delisting_event_return": -0.10} (a
    mild classified loss). Built so that, with the band's real -0.30/-0.55/-1.00 values, the
    FF3 alpha sign genuinely disagrees across band members (flip=True); if the classified
    -0.10 override wins instead (the regression this pins), every band member -- including
    the None entry -- collapses to the SAME alpha sign (flip=False)."""
    from shortlist.scout.daily import _DELISTING_BAND, _delisting_band_flip
    from shortlist.scout.validate import calendar_time_portfolio, ff3_alpha

    h = _hist("DEAD", [(date(2025, 1, 31), 100.0), (date(2025, 2, 10), 40.0)])
    ev = _ev("DEAD", "2025-01-31", meta={"delisting_event_return": -0.10})
    k_months = 12
    # rf pinned negative (-0.05) so the band's moderate loss (-0.30) clears it (positive
    # alpha) while the deeper losses (-0.55, -1.00) don't (negative alpha) -- a genuine
    # sign disagreement across band members, distinct from the classified -0.10's sign.
    ff3 = {
        f"2025-{m:02d}": (0.01 * ((m % 3) - 1), 0.001 * ((m % 2) - 1),
                          0.002 * ((m % 4) - 1.5), -0.05)
        for m in range(1, 13)
    }

    # Sanity check: the classified override and the blanket band value are genuinely
    # different measured returns at a fixed dr -- the two code paths are distinguishable.
    m_classified = measure_cohort([ev], "edgar:activist_13d", k_months, {"DEAD": h},
                                  delisting_return=-0.55, as_of=_AS_OF,
                                  use_event_delisting=True)
    m_blanket = measure_cohort([ev], "edgar:activist_13d", k_months, {"DEAD": h},
                               delisting_return=-0.55, as_of=_AS_OF,
                               use_event_delisting=False)
    assert abs(m_classified.events[0].ret - (-0.10)) < 1e-9
    assert abs(m_blanket.events[0].ret - (-0.55)) < 1e-9
    assert m_classified.events[0].ret != m_blanket.events[0].ret

    # Documentation of the masking mechanism: replaying the band loop with
    # use_event_delisting=True (the regression) collapses every member -- including the
    # None entry, since the classified value overrides regardless of the blanket dr -- to
    # the SAME alpha sign.
    signs_if_masked: set[int] = set()
    for dr in _DELISTING_BAND:
        m = measure_cohort([ev], "edgar:activist_13d", k_months, {"DEAD": h}, dr,
                           as_of=_AS_OF, use_event_delisting=True)
        ctp = calendar_time_portfolio(m.events, k_months, weighting="equal")
        alpha, _betas = ff3_alpha(ctp, ff3)
        if alpha is not None and alpha != 0:
            signs_if_masked.add(1 if alpha > 0 else -1)
    assert len(signs_if_masked) == 1, "fixture sanity: masked path must collapse to one sign"

    # The real pin: as wired (use_event_delisting=False inside the band call), the band's
    # fixed values genuinely disagree in sign -> flip=True. If daily.py's band call ever
    # drops/flips that kwarg to True, this assertion fails (flip becomes False).
    flip = _delisting_band_flip([ev], "edgar:activist_13d", k_months, {"DEAD": h}, ff3, _AS_OF)
    assert flip is True


def test_measurable_fraction_by_vintage():
    # 2020 vintage: both events measurable. 2024 vintage: mostly non-measurable
    # (one measurable, three not -> a real recent-vintage attrition, not fixture noise).
    h_2020a = _hist("A20", [(date(2020, 1, 31), 100.0), (date(2020, 4, 30), 110.0)])
    h_2020b = _hist("B20", [(date(2020, 6, 30), 50.0), (date(2020, 9, 30), 55.0)])
    h_2024a = _hist("A24", [(date(2024, 1, 31), 100.0), (date(2024, 4, 30), 90.0)])
    # the other three 2024 events have no series at all -> non-measurable
    evs = [
        _ev("A20", "2020-01-31"),
        _ev("B20", "2020-06-30"),
        _ev("A24", "2024-01-31"),
        _ev("GONE1", "2024-02-28"),
        _ev("GONE2", "2024-03-31"),
        _ev("GONE3", "2024-04-30"),
    ]
    m = measure_cohort(evs, "edgar:activist_13d", horizon_months=3,
                       hist_by_ticker={"A20": h_2020a, "B20": h_2020b, "A24": h_2024a},
                       delisting_return=None, as_of=_AS_OF)
    by_vintage = m.measurable_fraction_by_vintage()
    assert by_vintage[2020] == (2, 2, 1.0)
    n_meas_2024, n_sel_2024, frac_2024 = by_vintage[2024]
    assert n_sel_2024 == 4
    assert n_meas_2024 == 1
    assert abs(frac_2024 - 0.25) < 1e-9
