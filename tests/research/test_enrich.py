from shortlist.research import ResearchResult, enrich
from shortlist.research.models import FilingText, Moat, QualitativeAssessment


class _Card:
    def __init__(self, ticker, composite, gates=None, scored=True):
        self.ticker = ticker
        self.composite = composite
        self.gates = gates or []
        self.scored = scored

    @property
    def passed(self):
        return not self.gates and self.scored


def _assessment(ticker):
    return QualitativeAssessment(
        ticker=ticker, as_of="t", filing_accession=f"acc-{ticker}", filing_date="2025-10-31",
        model="claude-sonnet-4-6", cost_usd=0.05, moat=Moat(), synthesis=f"{ticker} read.")


CONFIG = {"research": {"output_root": "research"}}


def test_enrich_selects_top_n_non_gated(tmp_path):
    cards = [_Card("A", 90), _Card("B", 80, gates=["over_leveraged"]), _Card("C", 70)]
    seen = []
    def fake_fetch(ticker, **kw):
        return FilingText(ticker, f"acc-{ticker}", "2025-10-31", business="b")
    def fake_assess(card, filing, config, **kw):
        seen.append(card.ticker)
        return _assessment(card.ticker)
    cfg = {"research": {"output_root": str(tmp_path)}}
    results = enrich(cards, cfg, top_n=2, fetch=fake_fetch, assess_fn=fake_assess)
    assert seen == ["A", "C"]                 # B gated → skipped; top 2 non-gated
    assert all(isinstance(r, ResearchResult) for r in results)
    assert results[0].brief_path and results[0].cost_usd == 0.05


def test_enrich_skips_when_no_10k(tmp_path):
    cfg = {"research": {"output_root": str(tmp_path)}}
    results = enrich([_Card("A", 90)], cfg, top_n=1,
                     fetch=lambda t, **k: None, assess_fn=lambda *a, **k: None)
    assert results[0].skipped == "no 10-K"
    assert results[0].brief_path is None


def test_enrich_uses_cache_unless_refresh(tmp_path):
    from shortlist.research import report
    cfg = {"research": {"output_root": str(tmp_path)}}
    report.write(_assessment("A"), tmp_path)  # pre-seed cache for accession acc-A
    calls = {"n": 0}
    def fake_assess(card, filing, config, **kw):
        calls["n"] += 1
        return _assessment(card.ticker)
    fetch = lambda t, **k: FilingText(t, "acc-A", "2025-10-31", business="b")
    r = enrich([_Card("A", 90)], cfg, top_n=1, refresh=False, fetch=fetch, assess_fn=fake_assess)
    assert calls["n"] == 0 and r[0].brief_path and r[0].from_cache is True
    r2 = enrich([_Card("A", 90)], cfg, top_n=1, refresh=True, fetch=fetch, assess_fn=fake_assess)
    assert calls["n"] == 1                     # refresh forces re-assessment


def test_enrich_redacts_filing_fetch_errors(tmp_path):
    cfg = {"research": {"output_root": str(tmp_path)}}
    def boom(ticker, **kw):
        raise RuntimeError("edgar failed for token=sk-ant-SECRET123")
    results = enrich([_Card("A", 90)], cfg, top_n=1, fetch=boom,
                     assess_fn=lambda *a, **k: None)
    assert results[0].skipped.startswith("filing error:")
    assert "sk-ant-SECRET123" not in results[0].skipped   # redacted
    assert results[0].brief_path is None


def test_enrich_marks_assessment_failure(tmp_path):
    cfg = {"research": {"output_root": str(tmp_path)}}
    fetch = lambda t, **k: FilingText(t, f"acc-{t}", "2025-10-31", business="b")
    results = enrich([_Card("A", 90)], cfg, top_n=1, refresh=True,
                     fetch=fetch, assess_fn=lambda *a, **k: None)
    assert results[0].skipped == "assessment failed"
    assert results[0].brief_path is None
