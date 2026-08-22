"""The analyst-revision line reaches the prompt and the cache key correctly.

Unit rules live in test_analyst_revision_line.py. This file pins the cross-file
properties no single module enforces:
  1. the line renders inside QUANT CONTEXT, never as a filing segment (it is a
     computed number — CLAUDE.md: /deep grounding is per-segment);
  2. a disabled block leaves the prompt byte-identical;
  3. the rating drift moves the cache key, so a consensus that shifts after a brief
     was written does not serve the stale brief back.
"""
from shortlist.research.assess import _build_user_prompt
from shortlist.research.cachekey import _PROMPT_MODULES, context_digest
from shortlist.research.models import FilingBundle, FilingText
from shortlist.models import ScoreCard, StockMetrics


def _card(**kw):
    m = StockMetrics(ticker="AAPL", **kw)
    return ScoreCard(ticker="AAPL", composite=72.0, quality=80.0, moat=None,
                     growth=None, momentum=None, value=88.0, opportunity=88.0,
                     insider=None, metrics=m, sic_bucket="unknown")


def _bundle():
    tenk = FilingText("AAPL", "acc", "2026-02-20", business="b", mda="m", risk_factors="r")
    return FilingBundle(tenk=tenk, primary_accession="acc", cache_key="acc",
                        filing_date="2026-02-20")


def _cfg(enabled=True):
    return {"research": {"analyst_revision": {"enabled": enabled}}}


DRIFT = dict(rating_months=3, rating_buy_delta=-2,
             rating_hold_delta=1, rating_sell_delta=1)


# ------------------------------------------------------------------------- prompt

def test_line_renders_in_the_quant_context_block():
    prompt = _build_user_prompt(_bundle(), _cfg(), card=_card(**DRIFT))
    assert "Analyst revision" in prompt
    head = prompt.index("=== QUANT CONTEXT")
    assert prompt.index("Analyst revision") > head


def test_disabled_block_leaves_the_prompt_byte_identical():
    card = _card(**DRIFT)
    assert (_build_user_prompt(_bundle(), _cfg(enabled=False), card=card)
            == _build_user_prompt(_bundle(), {"research": {}}, card=card))


def test_the_line_is_not_a_filing_segment():
    """A computed number must not be quotable as filing evidence."""
    labels = dict(_bundle().segments())
    assert not any("revision" in k.lower() for k in labels)


# ---------------------------------------------------------------------- cache key

def test_module_is_fingerprinted_so_editing_it_invalidates_briefs():
    assert "analyst_revision" in _PROMPT_MODULES


def test_a_changed_drift_moves_the_context_digest():
    """Consensus moves monthly. Without this the cache would serve a brief whose
    revision line describes a window that has since rolled."""
    before = context_digest(_card(**DRIFT), None, _cfg())
    after = context_digest(_card(**{**DRIFT, "rating_buy_delta": -5}), None, _cfg())
    assert before != after


def test_a_disabled_block_does_not_move_the_digest():
    off = _cfg(enabled=False)
    assert (context_digest(_card(**DRIFT), None, off)
            == context_digest(_card(), None, off))
