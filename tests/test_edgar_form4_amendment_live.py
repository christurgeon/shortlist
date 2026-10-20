"""Live: aggregate_form4 counts a trade once when a Form 4/A restates it. Pinned by
accession so the assertions do not depend on what the latest filings happen to be."""
import os
from datetime import date

import pytest

from shortlist.providers._form4 import aggregate_form4

_PG_ORIGINAL = "0000080424-26-000158"    # one sale
_PG_AMENDMENT = "0000080424-26-000160"   # its 4/A: the same sale, trade date corrected


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
