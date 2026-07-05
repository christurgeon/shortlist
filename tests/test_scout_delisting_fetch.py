import os
from datetime import date
from types import SimpleNamespace

import pytest

import shortlist.scout.delisting as dl


class _FakeCompany:
    """Stands in for edgar.Company — records the ctor arg, returns canned filings."""
    last_ctor_arg = None

    def __init__(self, arg):
        _FakeCompany.last_ctor_arg = arg
        self._filings = [
            SimpleNamespace(accession_no="acc-1", form="8-K", filing_date=date(2023, 4, 24),
                            items="1.03,9.01", filer=None),
            SimpleNamespace(accession_no="acc-1", form="8-K", filing_date=date(2023, 4, 24),
                            items="1.03,9.01", filer=None),                    # duplicate row
            SimpleNamespace(accession_no="acc-2", form="25-NSE", filing_date=date(2023, 5, 4),
                            items=None, filer="The Nasdaq Stock Market LLC"),
        ]

    def get_filings(self, form=None):
        return list(self._filings)


def _install_fake_edgar(monkeypatch):
    fake_mod = SimpleNamespace(Company=_FakeCompany, set_identity=lambda s: None)
    monkeypatch.setitem(__import__("sys").modules, "edgar", fake_mod)


def test_fetch_builds_records_dedups_and_is_cik_keyed(monkeypatch):
    _install_fake_edgar(monkeypatch)
    recs = dl.fetch_filing_records("886158", "test@example.com")
    assert _FakeCompany.last_ctor_arg == 886158          # int CIK — NEVER a ticker string
    assert isinstance(_FakeCompany.last_ctor_arg, int)
    assert len(recs) == 2                                 # duplicate accession dropped
    eightk = next(r for r in recs if r.form == "8-K")
    assert eightk.items == ("1.03", "9.01")               # normalized at build time
    f25 = next(r for r in recs if r.form == "25-NSE")
    assert f25.filer == "The Nasdaq Stock Market LLC"
    # and the records classify end-to-end
    v = dl.classify_delisting(recs)
    assert v.reason == dl.BANKRUPTCY and v.terminal_return == -0.55


def test_fetch_failure_returns_none_with_warning(monkeypatch):
    class _Boom:
        def __init__(self, arg):
            raise RuntimeError("edgar down https://x?apikey=SECRET")

    fake_mod = SimpleNamespace(Company=_Boom, set_identity=lambda s: None)
    monkeypatch.setitem(__import__("sys").modules, "edgar", fake_mod)
    with pytest.warns(UserWarning, match="delisting") as rec:
        assert dl.fetch_filing_records(886158, "test@example.com") is None
    # the embedded key must be REDACTED by redact_secrets, never leaked into the warning
    text = "".join(str(w.message) for w in rec)
    assert "SECRET" not in text
    assert "apikey" in text          # the redacted URL skeleton is still there (context kept)


def test_fetch_malformed_cik_returns_none_never_raises(monkeypatch):
    _install_fake_edgar(monkeypatch)
    with pytest.warns(UserWarning):
        assert dl.fetch_filing_records("not-a-cik", "test@example.com") is None


def test_source_never_constructs_company_from_ticker():
    # Static guard (spec §12): every Company( call in the module is int()-cast.
    import inspect
    src = inspect.getsource(dl)
    for line in src.splitlines():
        if "Company(" in line and "class " not in line:
            assert "Company(int(" in line, f"non-CIK-keyed Company() call: {line.strip()}"


# --- live smoke (skipped by default; run with -m live, needs SEC_IDENTITY + edgar extra) -----

pytestmark_live = pytest.mark.skipif(
    not os.getenv("SEC_IDENTITY"), reason="needs SEC_IDENTITY + edgar extra")


@pytest.mark.live
@pytestmark_live
def test_live_bbby_classifies_bankruptcy():
    recs = dl.fetch_filing_records(886158, os.environ["SEC_IDENTITY"])   # BBBY subject CIK
    v = dl.classify_delisting(recs)
    assert v is not None and v.reason == dl.BANKRUPTCY
    # venue asserted EXPLICITLY: unknown-venue also yields -0.55, so pinning only the partial
    # would let a silently-broken Form-25 filer/header extraction pass.
    assert v.venue == "nasdaq"
    assert v.terminal_return == -0.55


@pytest.mark.live
@pytestmark_live
def test_live_atvi_classifies_mna():
    recs = dl.fetch_filing_records(718877, os.environ["SEC_IDENTITY"])   # ATVI subject CIK
    v = dl.classify_delisting(recs)
    assert v is not None and v.reason == dl.MNA
    assert v.terminal_return == 0.0
