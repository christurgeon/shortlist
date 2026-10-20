from __future__ import annotations

import collections
from datetime import date

import pytest

from shortlist.providers._form4 import (
    _frame_rows, aggregate_form4, summarize, classify_role, is_10b5_1,
)

# rows are (shares, price, code, date, name, role, planned)


def test_summarize_nets_buys_against_sells():
    rows = [
        (100, 10.0, "P", "2026-05-01", "Alice", "CEO", False),   # +1000 buy
        (50, 20.0, "S", "2026-05-02", "Bob", "CFO", False),       # -1000 sell
        (10, 5.0, "P", "2026-05-03", "Alice", "CEO", False),      # +50 buy
    ]
    s = summarize(rows)
    assert s.found
    assert s.net_value == 50.0
    assert s.buy_count == 2
    assert s.sell_count == 1
    assert len(s.txns) == 3
    # value is unsigned; kind carries direction (matches the FMP convention).
    assert s.txns[1].kind == "sell" and s.txns[1].value == 1000.0
    # v2 fields stay at no-signal defaults when conviction is not requested:
    assert s.distinct_buyers == 0
    assert s.role_weighted_buy_value == 0.0
    assert s.planned_sell_value == 0.0


def test_summarize_ignores_non_open_market_codes():
    # A=grant, M=option exercise, F=tax withholding, G=gift — not scored.
    rows = [(100, 10.0, c, "2026-05-01", "X", None, False) for c in ("A", "M", "F", "G")]
    s = summarize(rows)
    assert not s.found
    assert s.net_value == 0.0 and s.buy_count == 0 and s.sell_count == 0
    assert s.txns == []


def test_summarize_handles_missing_shares_or_price():
    rows = [
        (None, 10.0, "P", None, None, None, False),   # shares None -> value 0
        (100, None, "S", None, None, None, False),    # price None -> value 0
    ]
    s = summarize(rows)
    assert s.found                              # codes were valid
    assert s.net_value == 0.0
    assert s.txns[0].shares is None and s.txns[0].value == 0.0


def test_summarize_empty_is_not_found():
    s = summarize([])
    assert not s.found and s.net_value == 0.0 and s.txns == []


# --- _frame_rows (DataFrame extraction, with a pandas-free fake) ----------

_Row = collections.namedtuple(
    "Row", ["Shares", "Price", "Code", "Date", "footnotes",
            "Security", "Remaining", "DirectIndirect", "NatureOfOwnership"],
    defaults=(None, None, None, None))


def _row(shares, price, code, dt, footnotes=None):
    return _Row(shares, price, code, dt, footnotes)


def _trade(shares, price, code, dt, remaining, nature="By Trust", direct="I"):
    """A row carrying every field of the cross-filing trade key."""
    return _Row(shares, price, code, dt, None, "Common Stock", remaining, direct, nature)


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
    frame = _FakeFrame([_row(100, 10.0, "P", "2026-05-01")])
    rows = _frame_rows(frame, "Alice", "CEO")
    # 7-tuple now; planned defaults False when no resolver is supplied
    assert rows == [(100, 10.0, "P", "2026-05-01", "Alice", "CEO", False)]


def test_frame_rows_detects_10b5_1_via_resolver():
    frame = _FakeFrame([_row(100, 10.0, "S", "2026-05-01", footnotes="F1")])
    rows = _frame_rows(frame, "Bob", "CFO",
                       resolve_footnotes=lambda ids: "Sale under Rule 10b5-1 plan")
    assert rows[0][6] is True   # planned


# --- aggregate_form4 (filing loop, cutoff, parse-failure tolerance) -------

class _FakeOwner:
    def __init__(self, officer_title=None, is_director=False, is_officer=False, is_ten_pct_owner=False,
                 cik=None):
        self.cik = cik
        self.officer_title = officer_title
        self.is_director = is_director
        self.is_officer = is_officer
        self.is_ten_pct_owner = is_ten_pct_owner


class _FakeOwners:
    def __init__(self, owners):
        self.owners = owners


_OWN_CIK = 1067983


class _FakeIssuer:
    def __init__(self, cik):
        self.cik = cik


_OWN_ISSUER = _FakeIssuer("0001067983")


class _FakeForm4:
    def __init__(self, frame, insider_name, owners=None, footnotes="",
                 issuer=_OWN_ISSUER):
        self.market_trades = frame
        self.insider_name = insider_name
        self.reporting_owners = _FakeOwners(owners) if owners is not None else None
        self._footnotes = footnotes
        self.issuer = issuer

    def _resolve_footnotes(self, ids):
        return self._footnotes


class _FakeFiling:
    def __init__(self, filing_date, form4=None, raises=False, form="4"):
        self.filing_date = filing_date
        self._form4 = form4
        self._raises = raises
        self.form = form

    def obj(self):
        if self._raises:
            raise ValueError("unparseable")
        return self._form4


def test_aggregate_extracts_role_from_reporting_owner():
    cutoff = date(2026, 1, 1)
    f = _FakeFiling(date(2026, 5, 1), _FakeForm4(
        _FakeFrame([_row(100, 10.0, "P", "2026-05-01")]), "Alice",
        owners=[_FakeOwner(officer_title="Chief Executive Officer")]))
    s = aggregate_form4([f], cutoff, conviction=_CONV, issuer_cik=_OWN_CIK)
    assert s.txns[0].role == "Chief Executive Officer"
    assert s.role_weighted_buy_value == 1500.0   # 1.5 * 1000


def test_aggregate_handles_missing_reporting_owners():
    cutoff = date(2026, 1, 1)
    f = _FakeFiling(date(2026, 5, 1), _FakeForm4(
        _FakeFrame([_row(100, 10.0, "P", "2026-05-01")]), "Anon", owners=None))
    s = aggregate_form4([f], cutoff, issuer_cik=_OWN_CIK)   # no crash; role None
    assert s.buy_count == 1 and s.txns[0].role is None


def test_aggregate_stops_at_cutoff():
    cutoff = date(2026, 1, 1)
    recent = _FakeFiling(date(2026, 5, 1),
                         _FakeForm4(_FakeFrame([_row(100, 10.0, "P", "2026-05-01")]), "A"))
    stale = _FakeFiling(date(2025, 6, 1),
                        _FakeForm4(_FakeFrame([_row(999, 99.0, "P", "2025-06-01")]), "B"))
    s = aggregate_form4([recent, stale], cutoff, issuer_cik=_OWN_CIK)
    assert s.buy_count == 1 and s.net_value == 1000.0


def test_aggregate_skips_unparseable_filings():
    cutoff = date(2026, 1, 1)
    good = _FakeFiling(date(2026, 5, 2),
                       _FakeForm4(_FakeFrame([_row(50, 20.0, "S", "2026-05-02")]), "A"))
    bad = _FakeFiling(date(2026, 5, 1), raises=True)
    s = aggregate_form4([good, bad], cutoff, issuer_cik=_OWN_CIK)
    assert s.sell_count == 1 and s.net_value == -1000.0


def test_aggregate_excludes_filings_for_another_issuer():
    """A company's own Form 4 list also holds filings it made as reporting owner of
    another issuer's stock; those trades are not insider flow in the company."""
    cutoff = date(2026, 1, 1)
    own = _FakeFiling(date(2026, 5, 2), _FakeForm4(
        _FakeFrame([_row(100, 10.0, "P", "2026-05-02")]), "Alice",
        owners=[_FakeOwner(officer_title="CEO")]))
    other = _FakeFiling(date(2026, 5, 1), _FakeForm4(
        _FakeFrame([_row(1000, 50.0, "P", "2026-05-01")]), "HOLDCO INC",
        owners=[_FakeOwner(is_ten_pct_owner=True)], issuer=_FakeIssuer("0000000002")))
    s = aggregate_form4([own, other], cutoff, conviction=_CONV, issuer_cik=_OWN_CIK)
    assert s.buy_count == 1 and s.net_value == 1000.0
    assert [t.name for t in s.txns] == ["Alice"]
    assert s.role_weighted_buy_value == 1500.0


def test_aggregate_skips_an_amendment_that_restates_its_original():
    cutoff = date(2026, 1, 1)
    original = _FakeFiling(date(2026, 8, 24), _FakeForm4(
        _FakeFrame([_row(3053, 143.79, "S", "2026-08-19")]), "Aguilar"))
    amendment = _FakeFiling(date(2026, 8, 27), _FakeForm4(
        _FakeFrame([_row(3053, 143.79, "S", "2026-08-20")]), "Aguilar"), form="4/A")
    s = aggregate_form4([amendment, original], cutoff, issuer_cik=_OWN_CIK)
    assert s.sell_count == 1
    assert s.net_value == pytest.approx(-3053 * 143.79)


class _UnparseableAmendment(_FakeFiling):
    def obj(self):
        pytest.fail("a Form 4/A was parsed")


def test_aggregate_skips_an_amendment_without_parsing_it():
    cutoff = date(2026, 1, 1)
    amendment = _UnparseableAmendment(date(2026, 4, 22), form="4/A")
    kept = _FakeFiling(date(2026, 4, 21), _FakeForm4(
        _FakeFrame([_row(10, 5.0, "P", "2026-04-21")]), "Hill"))
    s = aggregate_form4([amendment, kept], cutoff, issuer_cik=_OWN_CIK)
    assert s.buy_count == 1 and s.net_value == 50.0


def _filed(day, rows, name, ciks=(), **kw):
    owners = [_FakeOwner(cik=c) for c in ciks] or None
    return _FakeFiling(date(2024, 5, day), _FakeForm4(_FakeFrame(rows), name, owners=owners, **kw))


def test_aggregate_counts_once_a_trade_that_joint_reporters_file_separately():
    sale = [_trade(3540250, 64.5545, "S", "2024-05-17", 230029024.0)]
    filings = [_filed(21, sale, "Jim C Walton", ciks=["0001"]),
               _filed(21, sale, "Alice L Walton", ciks=["0002"]),
               _filed(21, sale, "Robson S Walton", ciks=["0003"])]
    s = aggregate_form4(filings, date(2024, 1, 1), issuer_cik=_OWN_CIK)
    assert s.sell_count == 1
    assert s.net_value == pytest.approx(-3540250 * 64.5545)
    assert [t.name for t in s.txns] == ["Jim C Walton"]


def test_aggregate_counts_once_the_rows_of_a_form_filed_twice():
    rows = [_trade(100, 10.0, "S", "2024-05-01", 900.0, nature=None, direct="D"),
            _trade(200, 11.0, "S", "2024-05-02", 700.0, nature=None, direct="D")]
    s = aggregate_form4([_filed(3, rows, "Hoag", ciks=["0007"]), _filed(3, rows, "Hoag", ciks=["7"])],
                        date(2024, 1, 1), issuer_cik=_OWN_CIK)
    assert s.sell_count == 2 and s.net_value == -(1000.0 + 2200.0)


def test_aggregate_treats_a_nan_nature_of_ownership_as_absent():
    nan = float("nan")
    row = [_trade(100, 10.0, "S", "2024-05-01", 900.0, nature=nan, direct="D")]
    s = aggregate_form4([_filed(3, row, "Hoag", ciks=["7"]), _filed(2, row, "Hoag", ciks=["7"])],
                        date(2024, 1, 1), issuer_cik=_OWN_CIK)
    assert s.sell_count == 1


_TKO_BUY = [_trade(12531, 79.8, "P", "2023-11-14", 12531.0, nature=None, direct="D")]


def test_aggregate_keeps_identical_direct_buys_by_different_insiders():
    filings = [_filed(4, _TKO_BUY, "Ariel Emanuel", ciks=["0001320234"]),
               _filed(4, _TKO_BUY, "Mark S Shapiro", ciks=["0001345978"])]
    s = aggregate_form4(filings, date(2024, 1, 1), issuer_cik=_OWN_CIK)
    assert s.buy_count == 2
    assert [t.name for t in s.txns] == ["Ariel Emanuel", "Mark S Shapiro"]


def test_aggregate_merges_identical_direct_rows_when_a_reporting_owner_is_shared():
    filings = [_filed(4, _TKO_BUY, "Joint", ciks=["0001320234", "0001345978"]),
               _filed(4, _TKO_BUY, "Shapiro", ciks=["0001345978"])]
    s = aggregate_form4(filings, date(2024, 1, 1), issuer_cik=_OWN_CIK)
    assert s.buy_count == 1


def test_aggregate_never_merges_a_direct_row_from_a_filing_without_owners():
    for first, second in ((["0001"], ()), ((), ["0001"]), ((), ())):
        filings = [_filed(4, _TKO_BUY, "A", ciks=first), _filed(4, _TKO_BUY, "B", ciks=second)]
        s = aggregate_form4(filings, date(2024, 1, 1), issuer_cik=_OWN_CIK)
        assert s.buy_count == 2, (first, second)


def test_aggregate_merges_an_indirect_row_from_filings_without_owners():
    sale = [_trade(3540250, 64.5545, "S", "2024-05-17", 230029024.0)]
    s = aggregate_form4([_filed(21, sale, "A"), _filed(21, sale, "B")], date(2024, 1, 1),
                        issuer_cik=_OWN_CIK)
    assert s.sell_count == 1


def test_aggregate_never_merges_rows_within_one_filing():
    rows = [_trade(82, 728.1, "S", "2026-10-12", 5000.0, nature="By Olivan D LLC"),
            _trade(82, 728.1, "S", "2026-10-12", 5000.0, nature="By Reinhold D LLC"),
            _trade(45075, 128.28, "P", "2026-10-14", 377945.0),
            _trade(45075, 128.28, "P", "2026-10-14", 423020.0),
            _trade(10, 1.0, "S", "2026-10-15", 50.0),
            _trade(10, 1.0, "S", "2026-10-15", 50.0)]
    s = aggregate_form4([_FakeFiling(date(2026, 10, 16), _FakeForm4(_FakeFrame(rows), "A"))],
                        date(2026, 1, 1), issuer_cik=_OWN_CIK)
    assert s.sell_count == 4 and s.buy_count == 2


def test_aggregate_never_merges_rows_without_shares_owned_after():
    for remaining in (None, float("nan")):
        row = [_trade(100, 10.0, "S", "2024-05-01", remaining)]
        s = aggregate_form4([_filed(3, row, "A"), _filed(2, row, "B")],
                            date(2024, 1, 1), issuer_cik=_OWN_CIK)
        assert s.sell_count == 2, remaining


def test_aggregate_keeps_an_own_trade_identical_to_an_other_issuer_row():
    row = [_trade(100, 10.0, "S", "2024-05-01", 900.0)]
    other = _filed(3, row, "HOLDCO INC", issuer=_FakeIssuer("0000000002"))
    amendment = _FakeFiling(date(2024, 5, 3), _FakeForm4(_FakeFrame(row), "A"), form="4/A")
    s = aggregate_form4([other, amendment, _filed(2, row, "A")],
                        date(2024, 1, 1), issuer_cik=_OWN_CIK)
    assert s.sell_count == 1 and [t.name for t in s.txns] == ["A"]


def test_aggregate_matches_issuer_cik_regardless_of_padding_or_type():
    cutoff = date(2026, 1, 1)
    for filing_cik in ("0001067983", "1067983", 1067983):
        for want in (1067983, "0001067983"):
            f = _FakeFiling(date(2026, 5, 1), _FakeForm4(
                _FakeFrame([_row(100, 10.0, "P", "2026-05-01")]), "A",
                issuer=_FakeIssuer(filing_cik)))
            s = aggregate_form4([f], cutoff, issuer_cik=want)
            assert s.buy_count == 1, (filing_cik, want)


def test_aggregate_skips_filing_with_unreadable_issuer_cik():
    cutoff = date(2026, 1, 1)
    for issuer in (None, _FakeIssuer(""), _FakeIssuer(None), _FakeIssuer("n/a")):
        f = _FakeFiling(date(2026, 5, 1), _FakeForm4(
            _FakeFrame([_row(100, 10.0, "P", "2026-05-01")]), "A", issuer=issuer))
        s = aggregate_form4([f], cutoff, issuer_cik=_OWN_CIK)
        assert not s.found and s.net_value == 0.0, issuer


def test_aggregate_requires_issuer_cik():
    with pytest.raises(TypeError):
        aggregate_form4([], date(2026, 1, 1))


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


# --- conviction aggregates ---------------------------------------------------

_CONV = {
    "min_cluster_buy_value": 1000,
    "role_weights": {"c_suite": 1.5, "officer": 1.2, "director": 1.0, "ten_pct": 0.5, "unknown": 1.0},
}


def test_summarize_conviction_distinct_buyers_dedupes_and_floors():
    rows = [
        (100, 10.0, "P", "d", "Alice", "CEO", False),       # +1000, >= floor
        (100, 10.0, "P", "d", "Alice", "CEO", False),       # same Alice again -> 1 distinct
        (10, 10.0, "P", "d", "Bob", "Director", False),     # +100, BELOW floor -> excluded
        (100, 10.0, "P", "d", "Mega", "10% owner", False),  # 10%-owner -> excluded from cluster
    ]
    s = summarize(rows, _CONV)
    assert s.distinct_buyers == 1   # only Alice clears role + floor


def test_summarize_conviction_role_weighted_buys_only():
    rows = [
        (100, 10.0, "P", "d", "Alice", "CEO", False),    # +1.5*1000
        (100, 10.0, "S", "d", "Bob", "CEO", False),      # sell -> NOT role-weighted
    ]
    s = summarize(rows, _CONV)
    assert s.role_weighted_buy_value == 1500.0


def test_summarize_conviction_planned_sell_value():
    rows = [
        (100, 10.0, "S", "d", "Bob", "CFO", True),    # planned sell -> counted
        (100, 10.0, "S", "d", "Cy", "CFO", False),    # unplanned sell -> not counted
        (100, 10.0, "P", "d", "Al", "CFO", True),     # planned BUY -> never counted
    ]
    s = summarize(rows, _CONV)
    assert s.planned_sell_value == 1000.0


def test_summarize_conviction_none_is_noop():
    rows = [(100, 10.0, "P", "d", "Alice", "CEO", False)]
    s = summarize(rows)
    assert s.distinct_buyers == 0 and s.role_weighted_buy_value == 0.0 and s.planned_sell_value == 0.0
