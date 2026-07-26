from datetime import date
from pathlib import Path

from shortlist.scout.dera import dera_zip_url, parse_dera_tsvs
from shortlist.scout.insider import parse_form4_xml

FIX = Path(__file__).parent / "fixtures" / "form4"
SAMPLE = FIX / "dera_2025q1_sample"


def _dera():
    with (SAMPLE / "SUBMISSION.tsv").open() as s, \
         (SAMPLE / "REPORTINGOWNER.tsv").open() as o, \
         (SAMPLE / "NONDERIV_TRANS.tsv").open() as t:
        return parse_dera_tsvs(s, o, t)


def test_dera_url_shape():
    assert dera_zip_url("2025q1").endswith(
        "/insider-transactions-data-sets/2025q1_form345.zip")


def test_parses_dera_ddmonyyyy_dates_and_flags():
    buys = [t for t in _dera() if t.code == "P"]
    assert len(buys) == 1
    t = buys[0]
    assert t.date == date(2025, 3, 27)      # from "27-MAR-2025", NOT ISO
    assert t.plan_10b5_1 is False           # AFF10B5ONE is "0"
    assert "director" in t.roles            # from "Director" comma-joined string


def test_live_and_history_agree_on_the_same_filing():
    """THE guard: one real filing, both paths, same record.

    The encodings genuinely differ -- DERA has 27-MAR-2025 / '0' / 'Director', the XML
    has 2025-03-27 / '0' or 'false' / <isDirector> -- so this is not trivially true. It
    is the defence against live-vs-history definitional drift, the failure mode that
    broke the accruals leg.

    Exact equality on the CATEGORICAL fields: those are what drift corrupts (wrong
    column, wrong encoding, wrong sign). PRICE is compared with a tolerance because
    **DERA rounds TRANS_PRICEPERSHARE to 2dp while the XML carries full precision** --
    24.57 vs 24.5686 on this very filing (live-verified 2026-07-26).

    Do NOT "tighten" this to `==`: it will be permanently red. And do NOT normalise the
    XML down to 2dp to make it pass -- that discards real precision from the live path
    to satisfy a test. The rounding is immaterial to a $100k floor.
    """
    xml_t = [t for t in parse_form4_xml(
        (FIX / "oklo_0001104659-25-030072.xml").read_text(errors="replace"))
        if t.code == "P"][0]
    dera_t = [t for t in _dera() if t.code == "P"][0]

    for field in ("owner_cik", "ticker", "date", "code", "roles", "title", "plan_10b5_1"):
        assert getattr(xml_t, field) == getattr(dera_t, field), field
    assert xml_t.shares == dera_t.shares
    assert abs(xml_t.price - dera_t.price) < 0.01          # DERA 2dp rounding only
    assert abs(xml_t.value - dera_t.value) / dera_t.value < 1e-3
