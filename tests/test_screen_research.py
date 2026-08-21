import pytest

from shortlist.models import ScoreCard, StockMetrics
from shortlist.screen import _card_dict, build_arg_parser


def _wrap(ft):
    from shortlist.research.models import FilingBundle
    return FilingBundle(tenk=ft, primary_accession=ft.accession,
                        cache_key=ft.accession, filing_date=ft.filing_date)


def _card():
    return ScoreCard(ticker="AAPL", composite=70.0, quality=80, moat=80, growth=60,
                     momentum=10, value=20, opportunity=20, insider=50, gates=[],
                     metrics=StockMetrics(ticker="AAPL", price=100.0, target_median=120.0))


def test_parser_has_research_flags():
    ap = build_arg_parser()
    args = ap.parse_args(["--tickers", "AAPL", "--research", "5", "--refresh"])
    assert args.research == 5
    assert args.refresh is True


def test_research_defaults_to_none():
    ap = build_arg_parser()
    args = ap.parse_args(["--tickers", "AAPL"])
    assert args.research is None
    assert args.refresh is False


def test_card_dict_includes_research_path_when_present():
    d = _card_dict(_card(), research_paths={"AAPL": "research/AAPL/x.md"})
    assert d["research_path"] == "research/AAPL/x.md"


def test_card_dict_omits_research_path_when_absent():
    d = _card_dict(_card())
    assert "research_path" not in d


def test_run_research_phase_prints_maps_paths_and_handles_cache(capsys, monkeypatch):
    from shortlist import screen
    from shortlist.research import ResearchResult

    fake_results = [
        ResearchResult("AAPL", brief_path="research/AAPL/x.md", cost_usd=0.04, synthesis="Solid moat."),
        ResearchResult("MSFT", brief_path="research/MSFT/y.md", from_cache=True),
        ResearchResult("GEV", skipped="no 10-K"),
    ]
    monkeypatch.setattr(screen, "_research_available", lambda: True)
    monkeypatch.setattr(screen, "_run_enrich", lambda cards, cfg, n, refresh, macro=None: fake_results)

    paths = screen._run_research_phase([_card()], {"research": {}}, n=3, refresh=False)
    err = capsys.readouterr().err
    assert paths == {"AAPL": "research/AAPL/x.md", "MSFT": "research/MSFT/y.md"}
    assert "AAPL" in err and "Solid moat." in err
    assert "0.04" in err
    assert "cached" in err.lower()
    assert "no 10-K" in err


def test_run_research_phase_skips_when_unavailable(capsys, monkeypatch):
    from shortlist import screen
    monkeypatch.setattr(screen, "_research_available", lambda: False)
    paths = screen._run_research_phase([_card()], {}, n=2, refresh=False)
    assert paths == {}
    assert "skipping research" in capsys.readouterr().err.lower()


def test_research_rejects_non_positive():
    ap = build_arg_parser()
    for bad in ["0", "-1"]:
        with pytest.raises(SystemExit):
            ap.parse_args(["--tickers", "AAPL", "--research", bad])


def test_run_research_phase_survives_enrich_exception(capsys, monkeypatch):
    from shortlist import screen
    monkeypatch.setattr(screen, "_research_available", lambda: True)
    def boom(cards, cfg, n, refresh, macro=None):
        raise RuntimeError("edgar blew up with token=sk-ant-SECRET99")
    monkeypatch.setattr(screen, "_run_enrich", boom)
    paths = screen._run_research_phase([_card()], {}, n=1, refresh=False)
    assert paths == {}
    err = capsys.readouterr().err
    assert "research phase failed" in err
    assert "sk-ant-SECRET99" not in err          # redacted


def test_research_prompt_includes_short_interest_context():
    from shortlist.research.assess import _build_user_prompt
    from shortlist.research.models import FilingText
    from shortlist.models import StockMetrics, ScoreCard

    filing = FilingText(ticker="AAA", accession="x", filing_date="2026-01-01",
                        business="b", mda="m", risk_factors="r")
    card = ScoreCard(ticker="AAA", composite=50.0, quality=None, moat=None, growth=None,
                     momentum=None, value=None, opportunity=None, insider=None,
                     metrics=StockMetrics(ticker="AAA", short_pct_outstanding=0.12,
                                          days_to_cover=6.3, short_interest_rising=True))
    prompt = _build_user_prompt(_wrap(filing), {}, card)
    assert "QUANT CONTEXT" in prompt
    assert "12.0% of shares" in prompt and "6.3 days to cover" in prompt

    # No metrics -> no quant block, no crash.
    assert "QUANT CONTEXT" not in _build_user_prompt(_wrap(filing), {}, None)


def test_enrich_require_passed_false_includes_gated(monkeypatch):
    from shortlist.research import enrich
    from shortlist.models import ScoreCard

    # A gated card: passed is False (gates non-empty). require_passed=False must
    # still select it; default require_passed=True must skip it.
    gated = ScoreCard(ticker="LMT", composite=60.0, quality=50.0, moat=None,
                      growth=None, momentum=None, value=None, opportunity=None,
                      insider=None, gates=["over_leveraged"], scored=True)
    assert gated.passed is False

    seen = []
    def fake_fetch(ticker, **kw):      # real _enrich_card calls fetch(card.ticker) — ONE str arg
        seen.append(ticker)
        raise RuntimeError("no network in test")   # _enrich_card catches -> ResearchResult(skipped)

    # default (require_passed=True): gated card not selected -> fetch never called. It is
    # still REPORTED as skipped (a silently-empty result printed nothing at all).
    results = enrich([gated], {}, top_n=5, fetch=fake_fetch, assess_fn=lambda *a, **k: None)
    assert seen == []
    assert len(results) == 1 and results[0].brief_path is None
    assert "over_leveraged" in results[0].skipped

    # require_passed=False: gated card IS selected -> fetch called, a (skipped) ResearchResult
    # is returned end-to-end. Proves selection AND that the pipeline runs the gated name.
    results = enrich([gated], {}, top_n=5, require_passed=False,
                     fetch=fake_fetch, assess_fn=lambda *a, **k: None)
    assert seen == ["LMT"]
    assert len(results) == 1 and results[0].ticker == "LMT"


def test_enrich_none_bundle_uses_reason_fn():
    """A None bundle (no 10-K) routes through the injectable reason_fn, so a foreign
    issuer gets an ADR-aware skip reason instead of the bare 'no 10-K'."""
    from shortlist.research import enrich
    from shortlist.models import ScoreCard

    card = ScoreCard(ticker="NVO", composite=70.0, quality=60.0, moat=60.0,
                     growth=60.0, momentum=50.0, value=50.0, opportunity=50.0,
                     insider=50.0, gates=[], scored=True)
    results = enrich([card], {}, top_n=1, require_passed=False,
                     fetch=lambda ticker, **kw: None,
                     reason_fn=lambda t: f"{t}: files Form 20-F (foreign issuer)")
    assert len(results) == 1
    assert results[0].skipped == "NVO: files Form 20-F (foreign issuer)"
