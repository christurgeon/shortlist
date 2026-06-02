from unittest.mock import MagicMock, patch

from shortlist.providers.edgar import EdgarProvider


def test_edgar_provider_sets_and_tags_sic(monkeypatch):
    monkeypatch.setenv("SEC_IDENTITY", "test test@example.com")
    fake = MagicMock()
    fake.sic = 6211
    # `Company` is imported INSIDE fetch (`from edgar import Company`), so patch it
    # on the source module `edgar`. `aggregate_form4` is module-scope in edgar.py.
    no_insider = MagicMock()
    no_insider.found = False
    with patch("edgar.Company", return_value=fake), \
         patch("shortlist.providers.edgar.aggregate_form4", return_value=no_insider):
        m = EdgarProvider().fetch("SCHW")
    assert m.sic == "6211"
    assert m.sources.get("sic") == "edgar"
