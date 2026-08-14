"""`fetch_bundle` folds 8-K accessions into the FILING half of the brief cache key.

Selection is deliberately independent of `filing_events` (docs/audits/
2026-08-13-eightk-text-in-deep-design.md, F1), so `cachekey.context_digest`'s event
tuple cannot see an 8-K that falls outside EdgarSource's 40-row index — the measured
JPM case. Without these accessions in the key, a fresh earnings release only busts the
brief via the `max_age_days` day bucket, up to 24h late.
"""
import pytest

from shortlist.research import eightk, filings
from shortlist.research.models import EightKText, FilingText


def _tenk():
    return FilingText(ticker="JPM", accession="0000019617-26-000100",
                      filing_date="2026-02-20", business="b", mda="m", risk_factors="r")


def _eightk(accession, filed="2026-07-14"):
    return EightKText(accession=accession, filed=filed, items="2.02",
                      label=f"8-K {filed} (Item 2.02, EX-99.1)", text="results")


@pytest.fixture
def _offline(monkeypatch):
    """Stub every fetch fetch_bundle makes. The 10-Q lookup raises so the function's
    own try/except takes the degraded path — no network, no edgartools."""
    monkeypatch.setattr(filings, "fetch_10k", lambda *a, **k: _tenk())
    monkeypatch.setattr(filings, "_prior_year_sections", lambda *a, **k: ("", ""))

    import edgar

    def _no_network(*a, **k):
        raise RuntimeError("no network in unit tests")

    monkeypatch.setattr(edgar, "Company", _no_network)


def _key(monkeypatch, eightks):
    monkeypatch.setattr(eightk, "fetch_eightks", lambda *a, **k: eightks)
    return filings.fetch_bundle("JPM").cache_key


def test_no_eightks_leaves_the_key_byte_identical(_offline, monkeypatch):
    assert _key(monkeypatch, []) == "0000019617-26-000100"


def test_an_eightk_busts_the_key(_offline, monkeypatch):
    bare = _key(monkeypatch, [])
    with_8k = _key(monkeypatch, [_eightk("0000019617-26-000205")])
    assert with_8k != bare
    assert with_8k.endswith("+0000019617-26-000205")


def test_accessions_are_sorted_so_fetch_order_cannot_move_the_key(_offline, monkeypatch):
    a, b = _eightk("0000019617-26-000205"), _eightk("0000019617-26-000199")
    assert _key(monkeypatch, [a, b]) == _key(monkeypatch, [b, a])


def test_a_different_eightk_produces_a_different_key(_offline, monkeypatch):
    first = _key(monkeypatch, [_eightk("0000019617-26-000205")])
    second = _key(monkeypatch, [_eightk("0000019617-26-000206")])
    assert first != second


def test_an_eightk_without_an_accession_is_skipped(_offline, monkeypatch):
    assert _key(monkeypatch, [_eightk("")]) == "0000019617-26-000100"
