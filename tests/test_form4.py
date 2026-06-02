from __future__ import annotations

import collections
from datetime import date

from shortlist.providers._form4 import (
    _frame_rows, aggregate_form4, summarize, classify_role, is_10b5_1,
)

# rows are (shares, price, code, date, name, role)


def test_summarize_nets_buys_against_sells():
    rows = [
        (100, 10.0, "P", "2026-05-01", "Alice", "CEO"),   # +1000 buy
        (50, 20.0, "S", "2026-05-02", "Bob", "CFO"),       # -1000 sell
        (10, 5.0, "P", "2026-05-03", "Alice", "CEO"),      # +50 buy
    ]
    s = summarize(rows)
    assert s.found
    assert s.net_value == 50.0
    assert s.buy_count == 2
    assert s.sell_count == 1
    assert len(s.txns) == 3
    # value is unsigned; kind carries direction (matches the FMP convention).
    assert s.txns[1].kind == "sell" and s.txns[1].value == 1000.0


def test_summarize_ignores_non_open_market_codes():
    # A=grant, M=option exercise, F=tax withholding, G=gift — not scored.
    rows = [(100, 10.0, c, "2026-05-01", "X", None) for c in ("A", "M", "F", "G")]
    s = summarize(rows)
    assert not s.found
    assert s.net_value == 0.0 and s.buy_count == 0 and s.sell_count == 0
    assert s.txns == []


def test_summarize_handles_missing_shares_or_price():
    rows = [
        (None, 10.0, "P", None, None, None),   # shares None -> value 0
        (100, None, "S", None, None, None),    # price None -> value 0
    ]
    s = summarize(rows)
    assert s.found                              # codes were valid
    assert s.net_value == 0.0
    assert s.txns[0].shares is None and s.txns[0].value == 0.0


def test_summarize_empty_is_not_found():
    s = summarize([])
    assert not s.found and s.net_value == 0.0 and s.txns == []


# --- _frame_rows (DataFrame extraction, with a pandas-free fake) ----------

_Row = collections.namedtuple("Row", ["Shares", "Price", "Code", "Date"])


class _FakeFrame:
    """Mimics the bits of a pandas DataFrame that _frame_rows touches."""
    def __init__(self, rows):
        self._rows = rows
        self.empty = not rows

    def itertuples(self, index=False):
        return iter(self._rows)


def test_frame_rows_none_and_empty_return_empty():
    assert _frame_rows(None, "n", None) == []
    assert _frame_rows(_FakeFrame([]), "n", None) == []


def test_frame_rows_extracts_columns_and_injects_name_role():
    frame = _FakeFrame([_Row(100, 10.0, "P", "2026-05-01")])
    rows = _frame_rows(frame, "Alice", "CEO")
    assert rows == [(100, 10.0, "P", "2026-05-01", "Alice", "CEO")]


# --- aggregate_form4 (filing loop, cutoff, parse-failure tolerance) -------

class _FakeForm4:
    def __init__(self, frame, insider_name):
        self.market_trades = frame
        self.insider_name = insider_name


class _FakeFiling:
    def __init__(self, filing_date, form4=None, raises=False):
        self.filing_date = filing_date
        self._form4 = form4
        self._raises = raises

    def obj(self):
        if self._raises:
            raise ValueError("unparseable")
        return self._form4


def test_aggregate_stops_at_cutoff():
    cutoff = date(2026, 1, 1)
    recent = _FakeFiling(date(2026, 5, 1),
                         _FakeForm4(_FakeFrame([_Row(100, 10.0, "P", "2026-05-01")]), "A"))
    stale = _FakeFiling(date(2025, 6, 1),  # older than cutoff -> loop breaks here
                        _FakeForm4(_FakeFrame([_Row(999, 99.0, "P", "2025-06-01")]), "B"))
    s = aggregate_form4([recent, stale], cutoff)
    assert s.buy_count == 1 and s.net_value == 1000.0   # stale filing excluded


def test_aggregate_skips_unparseable_filings():
    cutoff = date(2026, 1, 1)
    good = _FakeFiling(date(2026, 5, 2),
                       _FakeForm4(_FakeFrame([_Row(50, 20.0, "S", "2026-05-02")]), "A"))
    bad = _FakeFiling(date(2026, 5, 1), raises=True)
    s = aggregate_form4([good, bad], cutoff)   # bad is skipped, not fatal
    assert s.sell_count == 1 and s.net_value == -1000.0


# --- classify_role + is_10b5_1 leaf helpers ---------------------------------

def test_classify_role_buckets():
    assert classify_role("Chief Executive Officer") == "c_suite"
    assert classify_role("CEO") == "c_suite"
    assert classify_role("EVP and CFO") == "c_suite"
    assert classify_role("Chief Financial Officer") == "c_suite"
    assert classify_role("President") == "officer"
    assert classify_role("EVP, Operations") == "officer"
    assert classify_role("director") == "director"
    assert classify_role("10% owner") == "ten_pct"
    assert classify_role(None) == "unknown"
    assert classify_role("") == "unknown"


def test_is_10b5_1_detects_footnote_text():
    assert is_10b5_1("Sale under a Rule 10b5-1 trading plan adopted 2025-01-01") is True
    assert is_10b5_1("Pursuant to a 10b5-1 plan") is True
    assert is_10b5_1("Gift to family trust") is False
    assert is_10b5_1("") is False
    assert is_10b5_1(None) is False
