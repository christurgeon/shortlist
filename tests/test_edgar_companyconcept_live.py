"""Live: the companyconcept reference filer still resolves, a filer that does not report
the diluted-shares tag reads as absence, and a reporter still returns its payload."""
import os

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (os.environ.get("RUN_LIVE_EDGAR") and os.environ.get("SEC_IDENTITY")),
        reason="live SEC call; set RUN_LIVE_EDGAR=1 and SEC_IDENTITY"),
]


@pytest.fixture(scope="module")
def src():
    from shortlist.data.sources import EdgarSource
    return EdgarSource()


def test_live_reference_concept_resolves(src):
    assert src._concept_reference_problem() == ""


def test_live_filer_without_tag_is_absence_not_error(src):
    errors: list[str] = []
    assert src._fetch_diluted_shares_concept("BRK-B", errors) == {}
    assert errors == []


def test_live_reporter_still_returns_payload(src):
    from shortlist.providers._gaap_tags import DILUTED_SHARES_TAG

    errors: list[str] = []
    payload = src._fetch_diluted_shares_concept("HON", errors)
    assert errors == []
    assert payload["tag"] == DILUTED_SHARES_TAG
    assert payload["units"]["shares"]
