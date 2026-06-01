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
