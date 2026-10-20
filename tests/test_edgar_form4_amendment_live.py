"""Live: aggregate_form4 counts a trade once when a Form 4/A restates it, when a form is
filed twice, and when joint reporters file it separately, but keeps identical direct buys by
different insiders. Pinned by accession so the assertions do not depend on what the latest
filings happen to be."""
import os
from datetime import date

import pytest

from shortlist.providers._form4 import aggregate_form4

_PG_ORIGINAL = "0000080424-26-000158"    # one sale
_PG_AMENDMENT = "0000080424-26-000160"   # its 4/A: the same sale, trade date corrected
_DUPLICATES = [
    ("NFLX", "0001062993-25-010708", "0001062993-25-010709"),   # one owner, filed twice
    ("WMT", "0001127602-23-030575", "0001127602-23-030576"),    # Jim and Alice Walton, one trust sale
]
_TKO_EMANUEL = "0000950170-23-063920"   # direct buy, 12,531 @ 79.80
_TKO_SHAPIRO = "0000950170-23-063915"   # a different insider, the identical direct buy


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("RUN_LIVE_EDGAR"), reason="live SEC call; set RUN_LIVE_EDGAR=1")
def test_live_amendment_does_not_double_count_its_original():
    from edgar import Company, set_identity
    set_identity(os.environ["SEC_IDENTITY"])

    company = Company("PG")
    filings = company.get_filings(form="4")
    original, amendment = filings.get(_PG_ORIGINAL), filings.get(_PG_AMENDMENT)
    assert original is not None and amendment is not None
    assert original.form == "4" and amendment.form == "4/A"
    assert not amendment.obj().market_trades.empty   # would be counted unfiltered

    cutoff = date(2000, 1, 1)
    once = aggregate_form4([original], cutoff, issuer_cik=company.cik)
    both = aggregate_form4([amendment, original], cutoff, issuer_cik=company.cik)
    assert both.sell_count == once.sell_count == 1
    assert both.net_value == once.net_value


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("RUN_LIVE_EDGAR"), reason="live SEC call; set RUN_LIVE_EDGAR=1")
@pytest.mark.parametrize("ticker,first,second", _DUPLICATES)
def test_live_identical_filings_count_once(ticker, first, second):
    from edgar import Company, set_identity
    set_identity(os.environ["SEC_IDENTITY"])

    company = Company(ticker)
    filings = company.get_filings(form="4")
    a, b = filings.get(first), filings.get(second)
    assert a is not None and b is not None
    assert a.form == b.form == "4"

    cutoff = date(2000, 1, 1)
    one = aggregate_form4([a], cutoff, issuer_cik=company.cik)
    other = aggregate_form4([b], cutoff, issuer_cik=company.cik)
    both = aggregate_form4([b, a], cutoff, issuer_cik=company.cik)
    assert one.sell_count >= 1 and other.sell_count == one.sell_count
    assert both.sell_count == one.sell_count
    assert both.net_value == pytest.approx(one.net_value)


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("RUN_LIVE_EDGAR"), reason="live SEC call; set RUN_LIVE_EDGAR=1")
def test_live_identical_direct_buys_by_different_insiders_both_count():
    from edgar import Company, set_identity
    set_identity(os.environ["SEC_IDENTITY"])

    company = Company("TKO")
    filings = company.get_filings(form="4")
    a, b = filings.get(_TKO_EMANUEL), filings.get(_TKO_SHAPIRO)
    assert a is not None and b is not None

    cutoff = date(2000, 1, 1)
    one = aggregate_form4([a], cutoff, issuer_cik=company.cik)
    both = aggregate_form4([a, b], cutoff, issuer_cik=company.cik)
    assert one.buy_count == 1
    assert both.buy_count == 2
    assert both.net_value == pytest.approx(2 * one.net_value)
