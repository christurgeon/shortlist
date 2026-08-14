"""Live-EDGAR contract test for the 8-K reader. Pins the edgartools attachment API
shapes the pure tests can only fake — `Filing.items`, `Filing.exhibits[].document_type`
and the lazily-parsed `.text()` — because CI pins our PARSE shape, not SEC's or
edgartools' (CLAUDE.md: `standard_concept` drift has broken extraction once already).

Skipped without SEC_IDENTITY (the [edgar] extra).
"""
import os
import re

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.getenv("SEC_IDENTITY"), reason="needs SEC_IDENTITY + edgar extra"),
]

# Measured in the 60-filing probe: 10/10 tickers had >=1 priority item in their last
# six 8-Ks, and AAPL/JPM file an EX-99 earnings release every quarter.
TICKER = "AAPL"


def _filings():
    from edgar import Company

    from shortlist.research.filings import require_identity
    require_identity()
    return list(Company(TICKER).get_filings(form="8-K"))[:6]


def test_items_field_is_populated_and_parseable():
    """60/60 probe rows: uniform `str`, comma-separated `d.dd`."""
    from shortlist.research.eightk import _codes

    rows = _filings()
    assert rows
    for f in rows:
        assert _codes(getattr(f, "items", None)), f"no item codes on {f.accession_no}"


def test_exhibit_attachments_expose_document_type_and_text():
    """EX-99 is the earnings-release convention; scoping to `EX-99*` is what dodges
    XOM's charter exhibits and NKE's 304,310-char EX-10.1 agreement."""
    from shortlist.research.eightk import _exhibit_text

    for f in _filings():
        for att in (getattr(f, "exhibits", None) or []):
            assert isinstance(str(att.document_type), str)
        dtype, text = _exhibit_text(f)
        if dtype:
            assert dtype.upper().startswith("EX-99")
            assert text and text == " ".join(text.split())   # normalized on ingest


def test_fetch_eightks_returns_labelled_bounded_text():
    from shortlist.research.eightk import config_block, fetch_eightks

    cfg = config_block(None)
    out = fetch_eightks(TICKER)
    assert out, "no qualifying 8-K in the lookback window — widen it or pick a filer"
    assert sum(len(e.text) for e in out) <= cfg["max_chars_total"]
    for e in out:
        assert e.text and len(e.text) <= cfg["max_chars_per_filing"]
        assert re.fullmatch(r"8-K \d{4}-\d{2}-\d{2} \(Item [\d., ]+(, [\w.\-]+)*\)", e.label)
        assert e.accession


def test_fetch_bundle_carries_the_8k_as_its_own_labelled_segment():
    """The wiring pin: `fetch_bundle` must actually populate `eightks`, and they must
    reach the haystack as their own segments (never folded into the 10-K's)."""
    from shortlist.research.filings import fetch_bundle

    b = fetch_bundle(TICKER)
    assert b is not None and b.eightks
    labels = [label for label, _ in b.segments()]
    assert labels[0] == "10-K"
    assert any(label.startswith("8-K ") for label in labels)
    assert b.haystack() == "\n\n".join(t for _, t in b.segments())
