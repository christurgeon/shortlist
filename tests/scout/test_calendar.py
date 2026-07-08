from datetime import date
from shortlist.scout.calendar import last_session, is_trading_day


def test_weekend_resolves_to_friday():
    # 2026-05-30 is a Saturday, 2026-05-31 a Sunday; 2026-05-29 is a Friday.
    assert last_session(date(2026, 5, 30)) == date(2026, 5, 29)
    assert last_session(date(2026, 5, 31)) == date(2026, 5, 29)


def test_holiday_resolves_backwards():
    # 2026-07-04 is Saturday -> observed Friday 2026-07-03 holiday; last session 07-02.
    assert last_session(date(2026, 7, 4)) == date(2026, 7, 2)


def test_trading_day_true_for_normal_weekday():
    assert is_trading_day(date(2026, 5, 29)) is True


def test_trading_day_false_for_new_years():
    assert is_trading_day(date(2026, 1, 1)) is False


def test_trading_day_false_for_2022_good_friday():
    assert is_trading_day(date(2022, 4, 15)) is False


def test_trading_day_true_for_weekday_adjacent_to_2022_good_friday():
    # 2022-04-14 is the Thursday immediately before Good Friday — a normal session.
    assert is_trading_day(date(2022, 4, 14)) is True


def test_trading_day_false_for_2022_juneteenth_observed():
    # 2022-06-19 (actual Juneteenth) was a Sunday; NYSE observed it Monday 2022-06-20 —
    # the first year NYSE closed for Juneteenth.
    assert is_trading_day(date(2022, 6, 20)) is False


def test_trading_day_false_for_2024_thanksgiving():
    assert is_trading_day(date(2024, 11, 28)) is False


def test_trading_day_false_for_2023_new_years_observed():
    # 2023-01-01 (actual New Year's Day) was a Sunday; observed Monday 2023-01-02.
    assert is_trading_day(date(2023, 1, 2)) is False


def test_trading_day_true_for_2022_jan_1_saturday_not_observed_adjacent():
    # 2022-01-01 (New Year's Day) fell on a Saturday and NYSE did NOT close an
    # adjacent weekday — both 2021-12-31 and 2022-01-03 traded normally. 2021 dates
    # aren't in the coverage table, so is_trading_day defaults to the weekday rule;
    # this test only pins the in-coverage side (2022-01-03).
    assert is_trading_day(date(2022, 1, 3)) is True


def test_last_session_walks_back_over_2022_long_weekend():
    # 2022-05-30 (Memorial Day, Monday) is a holiday; 2022-05-28/29 are a Sat/Sun.
    # The most recent completed session on or before 2022-05-30 is Friday 2022-05-27.
    assert last_session(date(2022, 5, 30)) == date(2022, 5, 27)
