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
        """edgartools EntityFilings-like. `latest` mirrors the real quirk: None for no
        rows and a bare filing for one row."""
        def __init__(self, items):
            self._items = list(items)

        def __len__(self):
            return len(self._items)

        def __iter__(self):
            return iter(self._items)

        def latest(self, n):
            seen.update(n=n, method="latest")
            top = self._items[:n]
            if not top:
                return None
            return top[0] if len(top) == 1 else _FakeFilings(top)

        def head(self, n):
            seen.update(n=n, method="head")
            return _FakeFilings(self._items[:n])

    class _FakeCompany:
        def __init__(self, ticker):
            seen["ticker"] = ticker
            self.cik = _OWN_CIK

        def get_filings(self, form, filing_date=None, sort_by=None):
            seen.update(form=form, filing_date=filing_date, sort_by=sort_by)
            return _FakeFilings(filings)

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


def test_fetch_insider_asks_only_for_the_lookback_window(monkeypatch):
    seen = _install_company(monkeypatch, [_filing("0001067983", "P", 1, 1.0, "Alice")])
    _src()._fetch_insider("BRK-B")
    assert seen["filing_date"] == f"{(date.today() - timedelta(days=183)).isoformat()}:"
    assert seen["sort_by"] == [("filing_date", "descending")]
    assert seen["method"] == "head"


def test_fetch_insider_counts_beyond_forty_filings(monkeypatch):
    _install_company(monkeypatch, [
        _filing("0001067983", "P", 1, 1.0, f"Insider {i}") for i in range(60)])
    res = _src()._fetch_insider("BRK-B")
    assert res.errors == []
    assert res.partial.insider.buy_count == 60


def test_fetch_insider_skips_form4_amendments(monkeypatch):
    amendment = _filing("0001067983", "S", 3053, 143.79, "Aguilar")
    amendment.form = "4/A"
    _install_company(monkeypatch, [amendment, _filing("0001067983", "S", 3053, 143.79, "Aguilar")])
    res = _src()._fetch_insider("PG")
    assert res.errors == []
    assert res.partial.insider.sell_count == 1
    assert len(res.raw["form4_trades"]) == 1


class _CountingFiling:
    def __init__(self, inner, counter):
        self._inner, self._counter = inner, counter
        self.filing_date = inner.filing_date

    def obj(self):
        self._counter.append(1)
        return self._inner.obj()


def test_fetch_insider_parses_at_most_the_cap(monkeypatch):
    parsed = []
    _install_company(monkeypatch, [
        _CountingFiling(_filing("0001067983", "P", 1, 1.0, f"Insider {i}"), parsed)
        for i in range(_FORM4_FETCH_LIMIT + 5)])
    res = _src()._fetch_insider("BRK-B")
    assert res.errors == []
    assert len(parsed) == _FORM4_FETCH_LIMIT
    assert res.raw.get("form4_truncated") is True


def test_fetch_insider_not_truncated_has_no_marker(monkeypatch):
    _install_company(monkeypatch, [_filing("0001067983", "P", 1, 1.0, "Alice")] * 3)
    res = _src()._fetch_insider("BRK-B")
    assert res.errors == []
    assert "form4_truncated" not in res.raw


def test_fetch_insider_with_no_form4_in_window_is_clean_and_empty(monkeypatch):
    """A filer with no Form 4 at all (e.g. a foreign private issuer) is not an error."""
    _install_company(monkeypatch, [])
    res = _src()._fetch_insider("ASML")
    assert res.errors == []
    assert res.partial.insider is None
