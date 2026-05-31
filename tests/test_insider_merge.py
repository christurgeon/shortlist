from __future__ import annotations

import pytest

from shortlist.data.models import (
    Insider, InsiderTxn, SourceResult, TickerSnapshot, merge_snapshots,
)
from shortlist.data.sources import build_sources


def _sr(source: str, insider: Insider) -> SourceResult:
    return SourceResult(source=source, partial=TickerSnapshot(ticker="X", insider=insider))


def test_edgar_flow_and_finnhub_sentiment_compose():
    # EDGAR supplies the authoritative transaction facts (no sentiment);
    # Finnhub supplies only sentiment. The merged object should carry both.
    edgar = _sr("edgar", Insider(
        net_value_6m=-9.0e6, buy_count=1, sell_count=6,
        recent=[InsiderTxn(kind="sell", shares=8000, price=1000, value=8.0e6)],
    ))
    finnhub = _sr("finnhub", Insider(sentiment_mspr=-0.25))

    merged = merge_snapshots("X", [finnhub, edgar], priority=["edgar", "finnhub"])
    ins = merged.insider
    assert ins.net_value_6m == -9.0e6
    assert ins.buy_count == 1 and ins.sell_count == 6
    assert ins.recent and ins.recent[0].kind == "sell"
    assert ins.sentiment_mspr == -0.25          # preserved from Finnhub
    assert set(merged.provenance["insider"]) == {"edgar", "finnhub"}


def test_coupled_transaction_group_is_never_mixed_across_sources():
    # Two sources both have transaction data. The coupled group (net/counts/
    # recent) must come ENTIRELY from the higher-priority source — never a
    # net_value from one glued to a buy_count from another.
    edgar = _sr("edgar", Insider(net_value_6m=-50.0, buy_count=0, sell_count=3,
                                 recent=[InsiderTxn(kind="sell", value=50.0)]))
    fmp = _sr("fmp", Insider(net_value_6m=100.0, buy_count=2, sell_count=0))

    merged = merge_snapshots("X", [fmp, edgar], priority=["edgar", "fmp"])
    ins = merged.insider
    assert ins.net_value_6m == -50.0            # all three from edgar...
    assert ins.buy_count == 0 and ins.sell_count == 3   # ...not 2/0 from fmp
    assert merged.provenance["insider"] == ["edgar"]


def test_sentiment_only_source_still_produces_insider():
    finnhub = _sr("finnhub", Insider(sentiment_mspr=0.1))
    merged = merge_snapshots("X", [finnhub], priority=["edgar", "finnhub"])
    assert merged.insider.sentiment_mspr == 0.1
    assert merged.insider.net_value_6m is None
    assert merged.provenance["insider"] == ["finnhub"]


def test_all_empty_insider_merges_to_none():
    a = _sr("edgar", Insider())          # nothing populated
    b = _sr("finnhub", Insider())
    merged = merge_snapshots("X", [a, b], priority=["edgar", "finnhub"])
    assert merged.insider is None
    assert "insider" not in merged.provenance


# --- registration / graceful degradation ---------------------------------

def test_edgar_is_registered_and_skips_gracefully_without_identity(monkeypatch, capsys):
    # No SEC_IDENTITY -> EdgarSource construction raises, build_sources skips it
    # (rather than crashing). 'edgar' must be a known source name regardless.
    monkeypatch.delenv("SEC_IDENTITY", raising=False)
    sources = build_sources(["edgar"])
    assert sources == []                        # skipped, not raised
    assert "edgar" in capsys.readouterr().out   # warned about the skip


def test_unknown_source_still_raises():
    with pytest.raises(ValueError):
        build_sources(["nope"])
