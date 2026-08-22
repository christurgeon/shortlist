"""Sell-side rating revision as a report cell, and in the `--json` card.

Conditional on both surfaces: a name with no derivable window, and a name whose
consensus did not move, add nothing a reader needs. The /deep prompt makes the
opposite call deliberately (an explicit "unchanged" there stops the model inferring
change from an absent line) — a glanceable card has no such failure mode.
"""
from datetime import date

from shortlist.models import ScoreCard, StockMetrics
from shortlist.bot.report.viewmodel import ReportVM, LeaderVM, MetricsVM, _metrics_vm
from shortlist.bot.report.sections import (
    render_html_body, render_text, Detail, _revision_text)
from shortlist.screen import _card_dict


def _leader(ticker, mvm):
    return LeaderVM(ticker=ticker, name=None, composite=70.0,
                    subscores={"quality": 70}, masked=set(), gates=[], flags=[],
                    confidence=0.8, thin=False, scored=True, coverage_note=None,
                    metrics=mvm, assessment=None)


def _vm(leaders):
    return ReportVM(session=date(2026, 6, 4), leaders=leaders, notes=[])


DRIFT = dict(rating_months=3, rating_buy_delta=-2,
             rating_hold_delta=1, rating_sell_delta=1)


# ------------------------------------------------------------------- the cell text

def test_metrics_vm_projects_the_revision():
    vm = _metrics_vm(StockMetrics(ticker="AAPL", **DRIFT))
    assert vm.rating_months == 3
    assert (vm.rating_buy_delta, vm.rating_hold_delta, vm.rating_sell_delta) == (-2, 1, 1)


def test_revision_text_signs_every_delta():
    assert _revision_text(MetricsVM(**DRIFT)) == "-2B / +1H / +1S · 3mo"


def test_revision_text_absent_without_a_window():
    assert _revision_text(MetricsVM()) is None
    assert _revision_text(MetricsVM(rating_months=0, rating_buy_delta=-2)) is None


def test_revision_text_absent_when_nothing_moved():
    assert _revision_text(MetricsVM(rating_months=3, rating_buy_delta=0,
                                    rating_hold_delta=0, rating_sell_delta=0)) is None


def test_revision_text_treats_a_missing_delta_as_zero():
    assert _revision_text(MetricsVM(rating_months=2, rating_buy_delta=1)) == \
        "+1B / +0H / +0S · 2mo"


# --------------------------------------------------------------- conditional render

def test_html_and_text_render_only_when_the_consensus_moved():
    body = render_html_body(_vm([_leader("AAPL", MetricsVM(**DRIFT))]))
    assert "Revision" in body and "-2B / +1H / +1S · 3mo" in body

    flat = render_html_body(_vm([_leader("X", MetricsVM(rating_months=3))]))
    assert "Revision" not in flat

    text = render_text(_vm([_leader("AAPL", MetricsVM(**DRIFT))]), Detail.FULL)
    assert "Revision: -2B / +1H / +1S · 3mo" in text


# ------------------------------------------------------------------------- --json

def _card(**kw):
    return ScoreCard(ticker="AAPL", composite=72.0, quality=80.0, moat=None,
                     growth=None, momentum=None, value=88.0, opportunity=88.0,
                     insider=None, metrics=StockMetrics(ticker="AAPL", **kw))


def test_card_dict_carries_the_revision_block():
    d = _card_dict(_card(**DRIFT))["analyst_revision"]
    assert d == {"months": 3, "buy_delta": -2, "hold_delta": 1, "sell_delta": 1}


def test_card_dict_omits_the_block_without_a_window():
    """A flat consensus IS reported here — unlike the card, the JSON has no
    glanceability cost and a downstream consumer needs zero distinguished from
    unfetched."""
    assert "analyst_revision" not in _card_dict(_card())
    assert _card_dict(_card(rating_months=3, rating_buy_delta=0,
                            rating_hold_delta=0,
                            rating_sell_delta=0))["analyst_revision"]["buy_delta"] == 0
