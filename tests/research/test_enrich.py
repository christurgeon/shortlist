from shortlist.research import ResearchResult, enrich
from shortlist.research.models import (FilingBundle, FilingText, Moat,
                                       QualitativeAssessment, Thesis)


class _Card:
    def __init__(self, ticker, composite, gates=None, scored=True):
        self.ticker = ticker
        self.composite = composite
        self.gates = gates or []
        self.scored = scored

    @property
    def passed(self):
        return not self.gates and self.scored


def _bundle(ticker, key=None):
    tenk = FilingText(ticker, f"acc-{ticker}", "2025-10-31", business="b")
    return FilingBundle(tenk=tenk, primary_accession=f"acc-{ticker}",
                        cache_key=key or f"acc-{ticker}", filing_date="2025-10-31")


def _assessment(ticker, key=None):
    return QualitativeAssessment(
        ticker=ticker, as_of="t", filing_accession=f"acc-{ticker}", filing_date="2025-10-31",
        model="claude-sonnet-4-6", cost_usd=0.05, moat=Moat(),
        thesis=Thesis(takeaway=f"{ticker} read."), cache_key=key or f"acc-{ticker}")


CONFIG = {"research": {"output_root": "research"}}


def test_enrich_selects_top_n_non_gated(tmp_path):
    cards = [_Card("A", 90), _Card("B", 80, gates=["over_leveraged"]), _Card("C", 70)]
    seen = []
    def fake_fetch(ticker, **kw):
        return _bundle(ticker)
    def fake_assess(card, bundle, config, **kw):
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
    report.write(_assessment("A"), tmp_path)  # pre-seed cache for cache_key acc-A
    calls = {"n": 0}
    def fake_assess(card, bundle, config, **kw):
        calls["n"] += 1
        return _assessment(card.ticker, key="acc-A")
    fetch = lambda t, **k: _bundle(t, key="acc-A")
    r = enrich([_Card("A", 90)], cfg, top_n=1, refresh=False, fetch=fetch, assess_fn=fake_assess)
    assert calls["n"] == 0 and r[0].brief_path and r[0].from_cache is True
    r2 = enrich([_Card("A", 90)], cfg, top_n=1, refresh=True, fetch=fetch, assess_fn=fake_assess)
    assert calls["n"] == 1                     # refresh forces re-assessment


def test_enrich_new_10q_invalidates_cache(tmp_path):
    from shortlist.research import report
    cfg = {"research": {"output_root": str(tmp_path)}}
    report.write(_assessment("A", key="acc-A+q1"), tmp_path)   # cached for q1
    calls = {"n": 0}
    def fake_assess(card, bundle, config, **kw):
        calls["n"] += 1
        return _assessment(card.ticker, key=bundle.cache_key)
    # a NEW 10-Q -> different composite key -> cache miss -> re-assessed
    fetch = lambda t, **k: _bundle(t, key="acc-A+q2")
    enrich([_Card("A", 90)], cfg, top_n=1, fetch=fetch, assess_fn=fake_assess)
    assert calls["n"] == 1


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
    fetch = lambda t, **k: _bundle(t)
    results = enrich([_Card("A", 90)], cfg, top_n=1, refresh=True,
                     fetch=fetch, assess_fn=lambda *a, **k: None)
    assert results[0].skipped == "assessment failed"


def test_enrich_isolates_assess_exception_without_aborting_batch(tmp_path):
    """An exception RAISED inside assess (not just fetch) must become a skipped
    result, never propagate and abort the batch — and the secret must be redacted."""
    cfg = {"research": {"output_root": str(tmp_path)}}
    fetch = lambda t, **k: _bundle(t)
    def boom_assess(card, bundle, config, **kw):
        if card.ticker == "A":
            raise RuntimeError("assess blew up token=sk-ant-SECRET999")
        return _assessment(card.ticker)
    results = enrich([_Card("A", 90), _Card("B", 80)], cfg, top_n=2,
                     refresh=True, fetch=fetch, assess_fn=boom_assess)
    assert len(results) == 2                                  # batch not aborted
    assert results[0].skipped.startswith("research error:")
    assert "sk-ant-SECRET999" not in results[0].skipped       # redacted
    assert results[1].brief_path                              # B still produced
    assert results[0].brief_path is None


def test_enrich_classifies_unregistered_ticker(tmp_path):
    # A ticker with no SEC CIK mapping (fund/ETF share class, e.g. VFLEX) makes
    # edgartools' Company() raise CompanyNotFoundError before the no-10-K path
    # can classify it. The skip reason must be human-readable, not the raw
    # library message with its Python-REPL "Tip:" line.
    class CompanyNotFoundError(Exception):  # matched by type NAME (edgar is optional)
        pass
    def fake_fetch(ticker, **kw):
        raise CompanyNotFoundError(
            f"Company not found: '{ticker}'\n  Tip: Search by name with "
            'find_company("...") or pass a CIK directly.')
    cfg = {"research": {"output_root": str(tmp_path)}}
    results = enrich([_Card("VFLEX", 90)], cfg, top_n=1,
                     fetch=fake_fetch, assess_fn=lambda *a, **k: None)
    assert results[0].skipped is not None
    assert "no SEC registrant" in results[0].skipped
    assert "Tip:" not in results[0].skipped
    assert results[0].brief_path is None


def test_enrich_other_fetch_errors_keep_filing_error_prefix(tmp_path):
    cfg = {"research": {"output_root": str(tmp_path)}}
    def fake_fetch(ticker, **kw):
        raise RuntimeError("boom")
    results = enrich([_Card("A", 90)], cfg, top_n=1,
                     fetch=fake_fetch, assess_fn=lambda *a, **k: None)
    assert results[0].skipped == "filing error: boom"
