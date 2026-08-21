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
        model="claude-sonnet-5", cost_usd=0.03, stop_reason="end_turn",
        business_model_summary="Sells devices.",
        moat=Moat(summary="Ecosystem.",
                  sources=[Finding("brand", "", verified=False)], trajectory="stable"),
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


def test_write_commits_md_last_so_no_stranded_cached_brief(tmp_path, monkeypatch):
    """The .md brief is the COMMIT MARKER (is_cached keys on it) and must be written
    LAST: a crash before the brief write leaves the JSON record on disk but the name
    still uncached, so the next run regenerates cleanly — never a 'cached' brief with
    no screening-call record."""
    import pytest
    a = _assessment()

    def boom(*args, **kwargs):
        raise RuntimeError("crash between the two writes")

    monkeypatch.setattr(report, "to_markdown", boom)
    with pytest.raises(RuntimeError):
        report.write(a, tmp_path)
    assert report.record_path("AAPL", a.filing_accession, tmp_path).exists()
    assert report.is_cached("AAPL", a.filing_accession, tmp_path) is False


def test_brief_never_renders_the_similarity_line():
    """The Lazy-Prices line is RETIRED (PLAN_INVENTORY_DECOMPOSITION §0.4): the metric
    retains stopwords, so every real YoY pair scored ~0.997 and the line always
    claimed "0% rewritten".
    text_similarity is still computed and still stored in the JSON — only the render
    is gone. Inverted from test_brief_renders_the_similarity_line."""
    import dataclasses

    from shortlist.research.models import Moat, QualitativeAssessment, Thesis
    from shortlist.research.report import to_markdown
    a = QualitativeAssessment(
        ticker="A", as_of="t", filing_accession="acc", filing_date="2026-01-01",
        model="m", cost_usd=0.0, moat=Moat(), thesis=Thesis(takeaway="t"),
        text_similarity=0.62)
    md = to_markdown(a)
    assert "Filing-text change" not in md and "Lazy Prices" not in md
    assert "38%" not in md
    # ...but it survives into the persisted record, which is what report.write stores.
    assert dataclasses.asdict(a)["text_similarity"] == 0.62


def test_brief_omits_the_similarity_line_when_none():
    from shortlist.research.models import Moat, QualitativeAssessment, Thesis
    from shortlist.research.report import to_markdown
    a = QualitativeAssessment(
        ticker="A", as_of="t", filing_accession="acc", filing_date="2026-01-01",
        model="m", cost_usd=0.0, moat=Moat(), thesis=Thesis(takeaway="t"))
    assert "Filing-text change" not in to_markdown(a)


def test_findings_source_suffix_only_appears_for_a_non_10k_document():
    """spec §3.5: the reader is told when a quote came from something OTHER than the
    10-K they already assume — and the brief stays byte-identical when it did not."""
    a = _assessment()
    plain = report.to_markdown(a)
    assert "— verified against" not in plain    # the unverified-count footer is a near-miss

    a.risks[0].source = "10-K"                       # the assumed default — still silent
    assert report.to_markdown(a) == plain

    a.risks[0].source = "8-K 2026-07-30 (Item 2.02, EX-99.1)"
    md = report.to_markdown(a)
    assert "_— verified against 8-K 2026-07-30 (Item 2.02, EX-99.1)_" in md
    assert "_(unverified)_" in md                     # the fabricated red flag is untouched


def test_an_unverified_finding_never_claims_a_source():
    a = _assessment()
    a.red_flags[0].source = "8-K 2026-07-30 (Item 2.02)"   # cannot happen; belt and braces
    md = report.to_markdown(a)
    assert "- **Invented** _(unverified)_" in md
