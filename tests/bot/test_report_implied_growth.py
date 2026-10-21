from datetime import date

import shortlist.bot.report as R
from shortlist.bot.report import build_report
from shortlist.bot.report.sections import (
    Detail, _implied_growth_text, render_html_body, render_text)
from shortlist.bot.report.viewmodel import LeaderVM, ReportVM, _metrics_vm, build_view_model
from shortlist.models import ScoreCard, StockMetrics
from shortlist.research.assess import _quant_context

_CFG = {"enabled": True, "discount_rate": 0.10, "base_years": 3,
        "run_rate_flag_ratio": 1.5, "display_floor": -0.50}


def _metrics(fcf, mcap=1000e9, revenue_cagr=None):
    return StockMetrics(ticker="AAPL", market_cap=mcap, revenue_cagr=revenue_cagr,
                        financial_series=[{"free_cash_flow": f} for f in fcf])


def _card(m):
    return ScoreCard(ticker="AAPL", composite=70.0, quality=70, moat=60, growth=50,
                     momentum=80, value=40, opportunity=80, insider=55, metrics=m)


def _text(m, cfg=_CFG):
    return _implied_growth_text(_metrics_vm(m, reverse_dcf_cfg=cfg))


def _leader(mvm):
    return LeaderVM(ticker="AAPL", name=None, composite=70.0,
                    subscores={"quality": 70}, masked=set(), gates=[], flags=[],
                    confidence=0.8, thin=False, scored=True, coverage_note=None,
                    metrics=mvm, assessment=None)


def _vm(mvm):
    return ReportVM(session=date(2026, 6, 4), leaders=[_leader(mvm)], notes=[])


def test_cell_compares_implied_growth_with_revenue_cagr():
    # base = median(10, 9, 8)B = 9B; g = 10% - 9B/1000B = 9.1%
    assert _text(_metrics([10e9, 9e9, 8e9], revenue_cagr=0.06)) == \
        "+9%/yr vs +6% rev CAGR · 10% disc"


def test_cell_omits_comparison_without_revenue_cagr():
    assert _text(_metrics([10e9, 9e9, 8e9])) == "+9%/yr · 10% disc"


def test_cell_shows_a_label_not_a_rate_when_distressed():
    # g = 10% - 700B/1000B = -60%, below the -50% display floor
    assert _text(_metrics([700e9], revenue_cagr=0.06)) == \
        "distressed (below -50%/yr) · 10% disc"


def test_cell_abstains_when_disabled_or_unconfigured():
    m = _metrics([10e9, 9e9, 8e9], revenue_cagr=0.06)
    assert _text(m, cfg={**_CFG, "enabled": False}) is None
    assert _text(m, cfg=None) is None
    assert _implied_growth_text(_metrics_vm(m)) is None


def test_cell_abstains_without_positive_fcf_or_market_cap():
    assert _text(_metrics([-1e9, -2e9, None])) is None
    assert _text(_metrics([10e9, 9e9, 8e9], mcap=None)) is None
    assert _text(StockMetrics(ticker="AAPL", market_cap=1000e9)) is None


def test_cell_rate_matches_the_prompt_line():
    m = _metrics([10e9, 9e9, 8e9, -3e9], mcap=150e9, revenue_cagr=0.06)
    card = _card(m)
    assert "~4%/yr" in _quant_context(card, rdcfg=_CFG)
    assert _text(m).startswith("+4%/yr")


def test_html_and_text_render_the_conditional_cell():
    mvm = _metrics_vm(_metrics([10e9, 9e9, 8e9], revenue_cagr=0.06), reverse_dcf_cfg=_CFG)
    body = render_html_body(_vm(mvm))
    assert "Implied growth" in body and "+9%/yr vs +6% rev CAGR · 10% disc" in body
    txt = render_text(_vm(mvm), Detail.FULL)
    assert "Implied growth: +9%/yr vs +6% rev CAGR · 10% disc" in txt

    bare = _metrics_vm(_metrics([10e9, 9e9, 8e9]))
    assert "Implied growth" not in render_html_body(_vm(bare))
    assert "Implied growth" not in render_text(_vm(bare), Detail.FULL)


def test_build_report_threads_reverse_dcf_config(monkeypatch):
    monkeypatch.setattr(R, "_render_png", lambda vm: None)
    card = _card(_metrics([10e9, 9e9, 8e9], revenue_cagr=0.06))
    art = build_report([card], date(2026, 6, 4), assessments={}, reverse_dcf_cfg=_CFG)
    assert "Implied growth: +9%/yr" in art.text
    art = build_report([card], date(2026, 6, 4), assessments={})
    assert "Implied growth" not in art.text


def test_bot_default_report_fn_uses_research_reverse_dcf_config(monkeypatch):
    from shortlist.bot.telegram import TelegramBot

    class _Notifier:
        def configured(self): return True

    monkeypatch.setattr(R, "_render_png", lambda vm: None)
    bot = TelegramBot(_Notifier(), {"research": {"reverse_dcf": _CFG}})
    card = _card(_metrics([10e9, 9e9, 8e9], revenue_cagr=0.06))
    art = bot._report_fn()([card], date(2026, 6, 4), assessments={})
    assert "Implied growth: +9%/yr" in art.text


def _scored(sic):
    from pathlib import Path

    import yaml

    from shortlist.scoring import score
    cfg = yaml.safe_load((Path(__file__).parents[2] / "config.yaml").read_text())
    m = _metrics([10e9, 9e9, 8e9], revenue_cagr=0.06)
    m.sic = sic
    return score(m, cfg), cfg["research"]["reverse_dcf"]


def test_cell_abstains_when_the_scorer_masks_fcf_for_the_sector():
    card, rdcfg = _scored("6021")   # national commercial bank
    assert card.sic_bucket != "unknown"
    assert any(a["field"] == "fcf_yield" for a in card.abstentions)
    vm = build_view_model([card], date(2026, 6, 4), assessments={}, reverse_dcf_cfg=rdcfg)
    assert _implied_growth_text(vm.leaders[0].metrics) is None


def test_cell_still_shown_for_an_unknown_bucket():
    card, rdcfg = _scored(None)
    assert card.sic_bucket == "unknown"
    vm = build_view_model([card], date(2026, 6, 4), assessments={}, reverse_dcf_cfg=rdcfg)
    assert _implied_growth_text(vm.leaders[0].metrics).startswith("+9%/yr")
