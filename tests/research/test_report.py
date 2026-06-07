import json

from shortlist.research import report
from shortlist.research.models import (
    Conflict,
    Finding,
    Moat,
    QualitativeAssessment,
    Thesis,
)


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
        thesis=Thesis(bull_case="Durable.", bear_case="Cyclical.",
                      what_would_change_my_mind=["margins compress"],
                      takeaway="Quality compounder."),
        reconciliation=[Conflict(signal="value", tension="cheap vs declining",
                                 filing_says="cash flow declined", verdict="contradicts",
                                 verified=True),
                        Conflict(signal="risk", tension="vol high", filing_says="",
                                 verdict="silent")],
        silent_count=1,
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
    for heading in ("## Thesis", "## Reconciliation", "## Moat", "## Business model",
                    "## Material risks", "## Red flags"):
        assert heading in md
    assert "analyst judgment" in md
    assert "_(filing silent)_" in md
    assert "Quality compounder." in md          # takeaway rendered
    assert "_(unverified)_" in md             # the fabricated red flag is flagged
    assert "1 claim" in md                     # unverified count surfaced


def test_write_creates_both_files_and_is_cached(tmp_path):
    a = _assessment()
    assert report.is_cached("AAPL", a.filing_accession, tmp_path) is False
    bp = report.write(a, tmp_path)
    assert bp.exists()
    assert report.record_path("AAPL", a.filing_accession, tmp_path).exists()
    assert report.is_cached("AAPL", a.filing_accession, tmp_path) is True
    saved = json.loads(report.record_path("AAPL", a.filing_accession, tmp_path).read_text())
    assert saved["ticker"] == "AAPL" and saved["moat"]["trajectory"] == "stable"


def test_write_json_record_has_synthesis_key(tmp_path):
    import json as _json
    a = _assessment()
    report.write(a, str(tmp_path))
    rec = report.record_path(a.ticker, a.filing_accession, str(tmp_path))
    data = _json.loads(rec.read_text())
    assert data["synthesis"] == "Quality compounder."   # injected (asdict drops the property)
    assert data["thesis"]["takeaway"] == "Quality compounder."
    assert data["reconciliation"][0]["signal"] == "value"


def test_write_keys_off_cache_key_when_set(tmp_path):
    from shortlist.research import report
    from shortlist.research.models import QualitativeAssessment, Moat, Thesis
    a = QualitativeAssessment(ticker="AAPL", as_of="t", filing_accession="acc10k",
                              filing_date="d", model="m", moat=Moat(), thesis=Thesis())
    a.cache_key = "acc10k+acc10q"
    bp = report.write(a, tmp_path)
    assert bp.name == "acc10k+acc10q.md"             # composite key drives the path
    assert report.is_cached("AAPL", "acc10k+acc10q", tmp_path) is True
    assert report.is_cached("AAPL", "acc10k", tmp_path) is False   # bare key != composite


def test_write_falls_back_to_accession_when_no_cache_key(tmp_path):
    from shortlist.research import report
    from shortlist.research.models import QualitativeAssessment, Moat, Thesis
    a = QualitativeAssessment(ticker="AAPL", as_of="t", filing_accession="acc10k",
                              filing_date="d", model="m", moat=Moat(), thesis=Thesis())
    bp = report.write(a, tmp_path)                    # cache_key == "" -> fallback
    assert bp.name == "acc10k.md"


def test_markdown_renders_added_risks(tmp_path):
    from shortlist.research import report
    from shortlist.research.models import (QualitativeAssessment, Moat, Thesis, Finding)
    a = QualitativeAssessment(ticker="A", as_of="t", filing_accession="acc",
                              filing_date="d", model="m", moat=Moat(), thesis=Thesis())
    a.added_risks = [Finding(claim="New cyber risk", evidence="A breach could harm us.",
                             verified=True)]
    md = report.to_markdown(a)
    assert "Newly disclosed risks" in md
    assert "New cyber risk" in md
