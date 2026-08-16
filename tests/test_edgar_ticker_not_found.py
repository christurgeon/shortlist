"""An unresolvable ticker should say so ONCE, clearly — not four times, unhelpfully.

Measured 2026-08-15: 8 of 238 tickers in the committed universes no longer resolve
because the issuer renamed its symbol or stopped filing (MMC -> MRSH, CSWI -> CSW,
UCBI -> UCB, LANC -> MZTI; AMED/CIVI/SCS delisted). Each of `_fetch_sync`'s four
isolated sections independently hit the same wall, so `EdgarSource.fetch("MMC")`
returned FOUR errors, each carrying edgartools' nearest-neighbour guess — for MMC
that guess is "MMCP (Mag Mile Capital)", an unrelated micro-cap.

Ticker resolution is a PRECONDITION, not one of the four isolated sections: if the
company cannot be resolved, none of them can succeed, so it is reported once and the
rest are skipped. See docs/audits/2026-08-14-tenq-mda-recovery-kill.md.
"""
import asyncio

import pytest

from shortlist.data.sources import EdgarSource
from shortlist.data.sources.edgar import _is_company_not_found


class _NotFound(Exception):
    """Shaped like edgartools' CompanyNotFoundError, which we must not import."""
    def __init__(self, ticker="MMC"):
        super().__init__(f"Company not found: {ticker!r}\n"
                         "  Similar: 'MMCP' (Mag Mile Capital, Inc.)\n"
                         '  Tip: Search by name with find_company("...") or pass a CIK directly.')


def _insider_not_found(self, ticker):
    """Faithful to the real `_fetch_insider`, which NEVER raises: it catches the
    edgartools error, appends it, and returns a SourceResult with a non-None partial."""
    from shortlist.data.models import SourceResult, TickerSnapshot
    r = SourceResult(source="edgar")
    r.errors.append(f"edgar: {_NotFound(ticker)}")
    r.partial = TickerSnapshot(ticker=ticker)
    return r


def _src():
    src = EdgarSource.__new__(EdgarSource)     # no __init__: no SEC identity, no network
    src.name = "edgar"
    src.lookback_days = 183
    src._conviction = None
    return src


def test_detects_the_not_found_shape_without_importing_edgartools():
    assert _is_company_not_found(_NotFound()) is True
    assert _is_company_not_found(RuntimeError("HTTP 503 from sec.gov")) is False
    assert _is_company_not_found(RuntimeError("")) is False


def test_unresolvable_ticker_reports_once_with_an_actionable_message(monkeypatch):
    monkeypatch.setattr(EdgarSource, "_fetch_insider", _insider_not_found, raising=False)
    res = _src()._fetch_sync("MMC")
    assert len(res.errors) == 1, f"expected one error, got {res.errors}"
    msg = res.errors[0]
    assert "MMC" in msg
    assert "renamed" in msg or "delisted" in msg      # tells the user what to do
    assert "Mag Mile" not in msg                       # not the nearest-neighbour noise
    assert res.partial is not None and res.partial.ticker == "MMC"
    assert res.partial.statements is None


def test_a_real_transient_failure_keeps_the_isolated_per_section_behaviour(monkeypatch):
    """Only a resolution failure short-circuits. A 503 must still let the other
    sections run and report independently — that isolation is deliberate."""
    from shortlist.data.models import Insider, SourceResult, TickerSnapshot

    def ok_insider(self, t):
        r = SourceResult(source="edgar")
        r.partial = TickerSnapshot(ticker=t, insider=Insider(buy_count=3, sell_count=0))
        return r
    monkeypatch.setattr(EdgarSource, "_fetch_insider", ok_insider, raising=False)
    monkeypatch.setattr(EdgarSource, "_fetch_sic", lambda self, t: "3711", raising=False)
    monkeypatch.setattr(EdgarSource, "_fetch_financials_object",
                        lambda self, t: (_ for _ in ()).throw(RuntimeError("SEC 503")), raising=False)
    monkeypatch.setattr(EdgarSource, "_fetch_filings_index", lambda self, t: [], raising=False)

    res = _src()._fetch_sync("AAPL")
    assert res.partial.insider.buy_count == 3
    assert res.partial.profile.sic == "3711"
    assert any("edgar-financials" in e for e in res.errors)


@pytest.mark.parametrize("ticker", ["MMC", "AMED"])
def test_fetch_surfaces_the_message_through_the_async_entry_point(monkeypatch, ticker):
    monkeypatch.setattr(EdgarSource, "_fetch_insider", _insider_not_found, raising=False)
    res = asyncio.run(EdgarSource.fetch(_src(), ticker))
    assert len(res.errors) == 1
    assert "SEC" in res.errors[0]
