"""Live SEC EDGAR — the only check that the real TenQ MD&A path and prior-10-K
selection work against actual filings (fakes pass while production fails). Skipped
unless SEC_IDENTITY is set and edgartools is installed."""
import os

import pytest

pytest.importorskip("edgar")
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("SEC_IDENTITY"),
        reason="needs SEC_IDENTITY (a contact email) for live EDGAR"),
]


def test_fetch_bundle_aapl_has_real_sections():
    from shortlist.research.filings import fetch_bundle
    b = fetch_bundle("AAPL")
    assert b is not None
    assert b.tenk.has_content()                  # current 10-K
    assert b.tenq_mda.strip() != ""              # real TenQ MD&A (guards B1)
    assert "+" in b.cache_key                     # composite (10-K + 10-Q)
