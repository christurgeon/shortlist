from datetime import date

from shortlist.scout.validate import MeasuredEvent, calendar_time_portfolio


def _m(ticker, d, ret):
    return MeasuredEvent("s", ticker, d, ret, ret is not None, 0.5, False, 60.0)


def test_ctp_one_event_spreads_over_holding_months():
    # one event, K=3, total return +33.1% -> monthly-equiv ~ (1.331)^(1/3)-1 = 0.10
    ev = _m("A", date(2025, 1, 15), 0.331)
    rows = calendar_time_portfolio([ev], k_months=3, weighting="equal")
    # held in Jan/Feb/Mar 2025 (trailing 3 months from each of those months)
    months = {mo for mo, _, _ in rows}
    assert {"2025-01", "2025-02", "2025-03"} <= months
    for mo, r, n in rows:
        if mo in {"2025-01", "2025-02", "2025-03"}:
            assert abs(r - 0.10) < 1e-6 and n == 1


def test_ctp_equal_weights_two_names_in_a_month():
    a = _m("A", date(2025, 3, 10), 0.0)       # monthly-equiv 0
    b = _m("B", date(2025, 3, 12), 0.0630)    # (1.063)^(1/1... use K=1) -> 0.063 at K=1
    rows = calendar_time_portfolio([a, b], k_months=1, weighting="equal")
    mar = [r for mo, r, n in rows if mo == "2025-03"]
    assert mar and abs(mar[0] - (0.0 + 0.0630) / 2) < 1e-6


def test_ctp_excludes_nonmeasurable():
    a = _m("A", date(2025, 3, 10), None)      # non-measurable
    a.measurable = False
    rows = calendar_time_portfolio([a], k_months=1)
    assert rows == []


def test_ctp_dedups_repeat_events_same_ticker_within_k_window():
    # A fires twice inside one K=3 holding window (Jan 15 and Feb 15 2025). Feb and Mar
    # would otherwise double-count A (once from each event) -- must collapse to one
    # contribution per ticker per month (the most-recent qualifying event), so n_names
    # stays 1 and the held return is the more recent event's monthly-equivalent, not an
    # average of both.
    ev1 = _m("A", date(2025, 1, 15), 0.0)
    ev2 = _m("A", date(2025, 2, 15), 0.0630)
    rows = calendar_time_portfolio([ev1, ev2], k_months=3, weighting="equal")
    by_month = {mo: (r, n) for mo, r, n in rows}

    assert by_month["2025-01"] == (0.0, 1)

    # Feb: both ev1 (age 1mo) and ev2 (age 0mo) qualify for the trailing-3 window --
    # dedup must keep exactly one contribution, from the more recent event (ev2).
    r_feb, n_feb = by_month["2025-02"]
    expected_feb = (1.0 + 0.0630) ** (1.0 / 3.0) - 1.0
    assert n_feb == 1
    assert abs(r_feb - expected_feb) < 1e-9

    # Mar: same two events both still qualify (ev1 age 2mo, ev2 age 1mo) -- dedup again.
    r_mar, n_mar = by_month["2025-03"]
    assert n_mar == 1
    assert abs(r_mar - expected_feb) < 1e-9

    # Apr: only ev2 qualifies (ev1 has aged out at exactly K=3 months).
    r_apr, n_apr = by_month["2025-04"]
    assert n_apr == 1
    assert abs(r_apr - expected_feb) < 1e-9


# --- real monthly paths, not an assumed constant one (audit 2026-07-26 §4) -------------
# `calendar_time_portfolio` used to give every held name a CONSTANT monthly rate
# `(1+ret)**(1/K)-1` for the whole holding window. A calendar-time portfolio is
# equal-weighted and rebalanced monthly, so assuming a smooth path for a name that
# actually collapsed in one month misstates every month's portfolio return: the crash
# gets spread across K months and the rebalancing drag is applied to it K times.

def _mp(ticker, d, ret, monthly):
    return MeasuredEvent("s", ticker, d, ret, True, 0.5, False, 60.0, False, monthly)


def test_ctp_month_return_uses_each_names_actual_month_return():
    """Two names held together. A compounds smoothly, B loses 90% in the FIRST month and
    is flat after. Month 0 must be the mean of (+10%, -90%); month 1 the mean of
    (+10%, 0%). The old constant-path code reported -21.8% for BOTH."""
    from datetime import date
    a = _mp("A", date(2025, 1, 10), 0.331, [0.10, 0.10, 0.10])
    b = _mp("B", date(2025, 1, 10), -0.90, [-0.90, 0.0, 0.0])
    rows = calendar_time_portfolio([a, b], k_months=3, weighting="equal")
    by_month = {mo: r for mo, r, _n in rows}
    assert abs(by_month["2025-01"] - (-0.40)) < 1e-9, by_month
    assert abs(by_month["2025-02"] - (+0.05)) < 1e-9, by_month
    assert abs(by_month["2025-03"] - (+0.05)) < 1e-9, by_month


def test_ctp_falls_back_to_flattened_rate_when_no_monthly_path():
    """Events with no monthly path (old persisted cohorts) keep the previous behaviour."""
    from datetime import date
    ev = MeasuredEvent("s", "A", date(2025, 1, 15), 0.331, True, 0.5, False, 60.0)
    rows = calendar_time_portfolio([ev], k_months=3, weighting="equal")
    for _mo, r, _n in rows:
        assert abs(r - 0.10) < 1e-6
