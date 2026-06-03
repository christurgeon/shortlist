import json

from shortlist.research import report
from shortlist.research.models import Finding, Moat, QualitativeAssessment


def _assessment():
    return QualitativeAssessment(
        ticker="AAPL", as_of="2026-05-31T00:00:00+00:00",
        filing_accession="0000320193-25-000123", filing_date="2025-10-31",
        model="claude-sonnet-4-6", cost_usd=0.03, stop_reason="end_turn",
        business_model_summary="Sells devices.",
        moat=Moat(summary="Ecosystem.", sources=["brand"], trajectory="stable"),
        risks=[Finding("Outsourced manufacturing", "outsourcing partners", verified=True)],
        red_flags=[Finding("Invented", "not in filing", verified=False)],
        management_capital_allocation="Buybacks.",
        synthesis="Quality compounder.",
        unverified_count=1,
    )


def test_paths_keyed_by_accession(tmp_path):
    bp = report.brief_path("aapl", "0000320193-25-000123", tmp_path)
    rp = report.record_path("aapl", "0000320193-25-000123", tmp_path)
    assert bp.name == "0000320193-25-000123.md"
    assert rp.name == "0000320193-25-000123.json"
    assert bp.parent.name == "AAPL"           # ticker upper-cased


def test_to_markdown_has_all_sections_and_disclaimer():
    md = report.to_markdown(_assessment())
    assert "LLM-generated" in md and "Not investment advice" in md
    assert "0000320193-25-000123" in md
    for heading in ("## Synthesis", "## Moat", "## Business model",
                    "## Management & capital allocation", "## Material risks", "## Red flags"):
        assert heading in md
    assert "_(unverified)_" in md             # the fabricated red flag is flagged
    assert "1 finding" in md                   # unverified count surfaced


def test_write_creates_both_files_and_is_cached(tmp_path):
    a = _assessment()
    assert report.is_cached("AAPL", a.filing_accession, tmp_path) is False
    bp = report.write(a, tmp_path)
    assert bp.exists()
    assert report.record_path("AAPL", a.filing_accession, tmp_path).exists()
    assert report.is_cached("AAPL", a.filing_accession, tmp_path) is True
    saved = json.loads(report.record_path("AAPL", a.filing_accession, tmp_path).read_text())
    assert saved["ticker"] == "AAPL" and saved["moat"]["trajectory"] == "stable"
