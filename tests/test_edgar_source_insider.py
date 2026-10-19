"""EdgarSource._fetch_insider counts only Form 4s whose issuer is the company itself."""
from datetime import date, timedelta

from shortlist.data.sources import EdgarSource
from shortlist.data.sources.edgar import _FORM4_FETCH_LIMIT

# Reuse the Form 4 fakes (sibling test module; pytest prepend-import).
from test_form4 import _FakeFiling, _FakeForm4, _FakeFrame, _FakeIssuer, _row

_OWN_CIK = 1067983


def _src():
    src = EdgarSource.__new__(EdgarSource)     # no __init__: no SEC identity, no network
    src.name = "edgar"
    src.lookback_days = 183
    src._conviction = None
    return src


def _install_company(monkeypatch, filings):
    seen = {}

    class _FakeFilings:
        def latest(self, n):
            seen["n"] = n
            return filings

    class _FakeCompany:
        def __init__(self, ticker):
            seen["ticker"] = ticker
            self.cik = _OWN_CIK

        def get_filings(self, form):
            seen["form"] = form
            return _FakeFilings()

    monkeypatch.setattr("edgar.Company", _FakeCompany)
    return seen


def _filing(issuer_cik, code, shares, price, name):
    d = date.today() - timedelta(days=1)
    return _FakeFiling(d, _FakeForm4(
        _FakeFrame([_row(shares, price, code, d.isoformat())]), name,
        issuer=_FakeIssuer(issuer_cik)))


def test_fetch_insider_counts_only_the_companys_own_form4s(monkeypatch):
    seen = _install_company(monkeypatch, [
        _filing("0001067983", "P", 100, 10.0, "Alice"),
        _filing("0000920760", "S", 1_000_000, 50.0, "BERKSHIRE HATHAWAY INC"),
    ])
    res = _src()._fetch_insider("BRK-B")
    ins = res.partial.insider
    assert res.errors == []
    assert ins.buy_count == 1 and ins.sell_count == 0
    assert ins.net_value_6m == 1000.0
    assert len(res.raw["form4_trades"]) == 1
    assert seen["form"] == "4" and seen["n"] == _FORM4_FETCH_LIMIT


def test_fetch_insider_with_only_other_issuer_filings_has_no_insider(monkeypatch):
    _install_company(monkeypatch, [
        _filing("0000920760", "S", 1_000_000, 50.0, "BERKSHIRE HATHAWAY INC"),
    ])
    res = _src()._fetch_insider("BRK-B")
    assert res.partial.insider is None
    assert res.errors == []
