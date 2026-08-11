"""Static US-equity market calendar — dependency-free.

Covers fixed + observed NYSE holidays for 2022-2027. A documented approximation
(no early closes, no ad-hoc closures); refresh the table when extending past 2027.
"""
from __future__ import annotations

from datetime import date, timedelta

# Observed NYSE full-day closures (already shifted to the observed weekday).
_HOLIDAYS: set[date] = {
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    # 2025
    date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17), date(2025, 4, 18),
    date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4), date(2025, 9, 1),
    date(2025, 11, 27), date(2025, 12, 25),
    # 2024
    date(2024, 1, 1), date(2024, 1, 15), date(2024, 2, 19), date(2024, 3, 29),
    date(2024, 5, 27), date(2024, 6, 19), date(2024, 7, 4), date(2024, 9, 2),
    date(2024, 11, 28), date(2024, 12, 25),
    # 2023
    date(2023, 1, 2), date(2023, 1, 16), date(2023, 2, 20), date(2023, 4, 7),
    date(2023, 5, 29), date(2023, 6, 19), date(2023, 7, 4), date(2023, 9, 4),
    date(2023, 11, 23), date(2023, 12, 25),
    # 2022 — New Year's Day fell on Saturday Jan 1 and was NOT observed on an
    # adjacent weekday (confirmed live: 2021-12-31 and 2022-01-03 both traded).
    # 2022 was also the first year NYSE observed Juneteenth (Jun 19 fell on a
    # Sunday, observed Monday Jun 20).
    date(2022, 1, 17), date(2022, 2, 21), date(2022, 4, 15), date(2022, 5, 30),
    date(2022, 6, 20), date(2022, 7, 4), date(2022, 9, 5), date(2022, 11, 24),
    date(2022, 12, 26),
    # 2027
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
}


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in _HOLIDAYS


def last_session(today: date) -> date:
    """The most recent completed trading session on or before `today` (today itself
    if it trades, else the prior trading day).

    The scout runs after the close, so 'today's session' if today trades, else
    walk back to the prior trading day.
    """
    d = today
    for _ in range(10):  # generous bound; never more than a long weekend + holidays
        if is_trading_day(d):
            return d
        d -= timedelta(days=1)
    raise RuntimeError(f"no trading day found within 10 days before {today}")
