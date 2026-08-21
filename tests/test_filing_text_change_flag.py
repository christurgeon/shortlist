"""Tests for the filing_text_change advisory flag (§4 "Lazy Prices").

Mirrors test_news_spike_flag: fires below the similarity threshold, no-op when
the config block is absent (byte-identical), and never touches the composite."""
from pathlib import Path

import yaml

from shortlist.models import StockMetrics
from shortlist.scoring import check_flags, score

CFG_FLAGS = {"filing_text_change": {"max_similarity": 0.7}}


def _m(**kw):
    return StockMetrics(ticker="AAPL", **kw)


def test_fires_when_similarity_below_threshold():
    # Big YoY rewrite (low similarity) -> flag.
    assert "filing_text_change" in check_flags(
        _m(filing_text_similarity=0.55), CFG_FLAGS)


def test_no_fire_when_similarity_high():
    # Lazy (copied) filing -> high similarity -> benign, no flag.
    assert "filing_text_change" not in check_flags(
        _m(filing_text_similarity=0.95), CFG_FLAGS)


def test_no_fire_at_exact_threshold():
    # Strictly-below comparison: == threshold does NOT fire.
    assert "filing_text_change" not in check_flags(
        _m(filing_text_similarity=0.7), CFG_FLAGS)


def test_no_fire_when_metric_absent():
    # No similarity computed (the default screen path) -> never fires.
    assert "filing_text_change" not in check_flags(_m(), CFG_FLAGS)


def test_noop_when_config_block_absent():
    # No filing_text_change block -> never fires even with a low similarity
    # (the byte-identical back-compat guarantee).
    assert "filing_text_change" not in check_flags(
        _m(filing_text_similarity=0.1), {})


def _cfg_with_flag_enabled() -> dict:
    """The shipped config with filing_text_change force-enabled.

    The shipped block is `enabled: false` (2026-08-20 — the flag has no producer AND
    the metric cannot reach its threshold; see config.yaml). These tests still pin the
    RULE's behaviour for the day someone fixes both, so they inject the enabled block
    rather than reading the shipped state. `test_shipped_config_never_fires_the_flag`
    below pins the shipped state itself."""
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    cfg["flags"]["filing_text_change"] = {"max_similarity": 0.7}
    return cfg


def test_metric_does_not_change_composite():
    cfg = _cfg_with_flag_enabled()
    base = StockMetrics(ticker="AAPL", gross_margin=0.4, net_margin=0.25,
                        revenue=4e11, market_cap=3e12)
    changed = StockMetrics(ticker="AAPL", gross_margin=0.4, net_margin=0.25,
                           revenue=4e11, market_cap=3e12,
                           filing_text_similarity=0.3)
    a, b = score(base, cfg), score(changed, cfg)
    assert a.composite == b.composite                       # never touches composite
    assert "filing_text_change" in (b.flags or [])          # but the advisory fires


def test_disabled_config_is_byte_identical_card():
    # Removing the filing_text_change block yields a card whose flags omit it and
    # whose composite/scored/passed are unchanged vs the no-metric baseline.
    cfg = _cfg_with_flag_enabled()
    cfg_off = _cfg_with_flag_enabled()
    cfg_off["flags"].pop("filing_text_change", None)
    m = StockMetrics(ticker="AAPL", gross_margin=0.4, net_margin=0.25,
                     revenue=4e11, market_cap=3e12, filing_text_similarity=0.2)
    on, off = score(m, cfg), score(m, cfg_off)
    assert "filing_text_change" in (on.flags or [])
    assert "filing_text_change" not in (off.flags or [])
    assert on.composite == off.composite
    assert on.scored == off.scored and on.passed == off.passed


def test_shipped_config_never_fires_the_flag():
    """The SHIPPED config must not emit filing_text_change, however low the similarity.

    Guards the 2026-08-20 retirement: `enabled: false` on the block. If someone flips
    it back on without also supplying a producer for filing_text_similarity and fixing
    the ~0.9 cosine floor, this fails and points them at the reasons."""
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    assert cfg["flags"]["filing_text_change"]["enabled"] is False
    m = StockMetrics(ticker="AAPL", gross_margin=0.4, net_margin=0.25,
                     revenue=4e11, market_cap=3e12, filing_text_similarity=0.0)
    assert "filing_text_change" not in (score(m, cfg).flags or [])
