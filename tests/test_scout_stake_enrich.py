"""Tests for initial-13D meta enrichment (stake-% in emission metadata)."""

from shortlist.scout.edgar_index import activist_stakes_from_records

RECS = [
    {
        "ticker": "TGT",
        "cik": "0000000123",
        "subject_name": "Target Co",
        "activist": "Fund LP",
        "form": "SCHEDULE 13D",
        "accession": "a1",
    }
]


def test_default_is_byte_identical():
    plain = activist_stakes_from_records(RECS)
    explicit = activist_stakes_from_records(RECS, stake_by_accession=None)
    assert [
        (e.ticker, e.strength, e.evidence, e.meta) for e in plain
    ] == [(e.ticker, e.strength, e.evidence, e.meta) for e in explicit]
    assert plain[0].meta == {}


def test_enrichment_adds_meta_only():
    plain = activist_stakes_from_records(RECS)[0]
    rich = activist_stakes_from_records(RECS, stake_by_accession={"a1": 7.2})[0]
    assert rich.meta == {"stake_pct": 7.2}
    assert (rich.ticker, rich.strength, rich.evidence, rich.cik) == (
        plain.ticker,
        plain.strength,
        plain.evidence,
        plain.cik,
    )  # strength UNTOUCHED
