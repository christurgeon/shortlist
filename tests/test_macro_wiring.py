# tests/test_macro_wiring.py
from __future__ import annotations
import inspect
import shortlist.scout.daily as daily


def test_daily_imports_fetch_macro():
    # the daily flow must reference fetch_macro so the overlay reaches the report
    src = inspect.getsource(daily)
    assert "fetch_macro" in src
