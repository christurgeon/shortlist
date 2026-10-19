"""Live: aggregate_form4 drops a Form 4 that Berkshire filed as reporting owner of
another issuer (Lennar) and keeps one filed for Berkshire's own stock. Pinned by
accession so the assertions do not depend on what the latest filings happen to be."""
import os
from datetime import date

import pytest

from shortlist.providers._form4 import _issuer_cik, aggregate_form4

_OTHER_ISSUER_ACCESSION = "0001193125-26-414744"   # Berkshire buying LENNAR CORP
_OWN_ISSUER_ACCESSION = "0001728451-26-000004"     # Berkshire officer selling BRK


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("RUN_LIVE_EDGAR"), reason="live SEC call; set RUN_LIVE_EDGAR=1")
def test_live_other_issuer_form4_is_excluded_and_own_is_kept():
    from edgar import Company, set_identity
    set_identity(os.environ["SEC_IDENTITY"])

    company = Company("BRK-B")
    filings = company.get_filings(form="4")
    other = filings.get(_OTHER_ISSUER_ACCESSION)
    own = filings.get(_OWN_ISSUER_ACCESSION)
    assert other is not None and own is not None

    other_form4 = other.obj()
    assert _issuer_cik(other_form4) not in (None, int(company.cik))
    assert not other_form4.market_trades.empty   # would have been counted unfiltered
    assert _issuer_cik(own.obj()) == int(company.cik)

    cutoff = date(2000, 1, 1)
    assert not aggregate_form4([other], cutoff, issuer_cik=company.cik).found
    kept = aggregate_form4([own], cutoff, issuer_cik=company.cik)
    assert kept.found and kept.sell_count >= 1
