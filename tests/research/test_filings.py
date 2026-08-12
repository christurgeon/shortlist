from shortlist.research.filings import _build_filing_text
from shortlist.research.models import FilingText


class _FakeTenK:
    def __init__(self, business=None, mda=None, risk=None):
        self.business = business
        self.management_discussion = mda
        self.risk_factors = risk


def test_build_filing_text_maps_sections():
    tenk = _FakeTenK(business="We make widgets.", mda="Revenue rose.", risk="Supply risk.")
    ft = _build_filing_text("AAPL", "0000320193-25-000123", "2025-10-31", tenk)
    assert isinstance(ft, FilingText)
    assert ft.ticker == "AAPL"
    assert ft.accession == "0000320193-25-000123"
    assert ft.filing_date == "2025-10-31"
    assert ft.business == "We make widgets."
    assert ft.mda == "Revenue rose."
    assert ft.risk_factors == "Supply risk."


def test_build_filing_text_tolerates_missing_sections():
    tenk = _FakeTenK(business="Only business section.", mda=None, risk=None)
    ft = _build_filing_text("X", "a", "d", tenk)
    assert ft.business == "Only business section."
    assert ft.mda == "" and ft.risk_factors == ""
    assert ft.has_content() is True


def test_build_filing_text_all_empty_has_no_content():
    ft = _build_filing_text("X", "a", "d", _FakeTenK())
    assert ft.has_content() is False


def test_cap_sections_prefix_trims_and_is_noop_when_absent():
    from shortlist.research.filings import cap_sections
    from shortlist.research.models import FilingText
    f = FilingText(ticker="X", accession="a", filing_date="d",
                   business="b"*100, mda="m"*100, risk_factors="r"*100)
    # absent caps -> unchanged (same object semantics: identical content)
    assert cap_sections(f, None) is f
    assert cap_sections(f, {}) is f
    capped = cap_sections(f, {"business": 10, "risk_factors": 40})
    assert len(capped.business) == 10           # trimmed
    assert len(capped.mda) == 100               # no cap given -> untouched
    assert len(capped.risk_factors) == 40
    # prompt == haystack consistency: combined() reflects the trim
    assert capped.business in capped.combined()


def test_cap_sections_under_cap_unchanged():
    from shortlist.research.filings import cap_sections
    from shortlist.research.models import FilingText
    f = FilingText(ticker="X", accession="a", filing_date="d",
                   business="short", mda="short", risk_factors="short")
    capped = cap_sections(f, {"business": 1000})
    assert capped.business == "short"


def test_cap_bundle_caps_tenk_and_tenq():
    from shortlist.research.filings import cap_bundle
    from shortlist.research.models import FilingBundle, FilingText
    tenk = FilingText("X", "a", "d", business="b"*100, mda="m"*100, risk_factors="r"*100)
    b = FilingBundle(tenk=tenk, primary_accession="a", cache_key="a", filing_date="d",
                     tenq_mda="q"*100, added_risks_text="kept")
    capped = cap_bundle(b, {"business": 10, "tenq_mda": 20})
    assert len(capped.tenk.business) == 10
    assert len(capped.tenq_mda) == 20
    assert capped.added_risks_text == "kept"      # not capped here (riskdiff caps it)
    # haystack reflects the trim (prompt == haystack invariant)
    assert capped.tenk.business in capped.haystack()
    assert capped.tenq_mda in capped.haystack()

def test_cap_bundle_noop_when_absent():
    from shortlist.research.filings import cap_bundle
    from shortlist.research.models import FilingBundle, FilingText
    b = FilingBundle(tenk=FilingText("X", "a", "d", business="b"), primary_accession="a",
                     cache_key="a", filing_date="d", tenq_mda="q")
    assert cap_bundle(b, None) is b

def test_tenq_mda_uses_part_i_item_2():
    # Real TenQ exposes MD&A via get_item_with_part, NOT a `management_discussion` attr.
    from shortlist.research.filings import _tenq_mda
    class _FakeTenQ:
        def get_item_with_part(self, part, item, markdown=True):
            assert (part, item) == ("Part I", "Item 2")
            return "Quarterly MD&A text."
    assert _tenq_mda(_FakeTenQ()) == "Quarterly MD&A text."

def test_tenq_mda_empty_on_missing():
    from shortlist.research.filings import _tenq_mda
    class _Bare:
        def get_item_with_part(self, *a, **k):
            return None
    assert _tenq_mda(_Bare()) == ""
    assert _tenq_mda(object()) == ""              # no method at all -> ""


class _PriorTenK:
    risk_factors = "prior risk text"
    management_discussion = "prior mda text"


class _FakeFiling:
    form = "10-K"
    def __init__(self, filing_date, period):
        self.filing_date, self.period_of_report = filing_date, period
        self.obj_calls = 0
    def obj(self):
        self.obj_calls += 1
        return _PriorTenK()


def _fake_company(rows):
    class _Company:
        def __init__(self, ticker):
            self.ticker = ticker
        def get_filings(self, form):
            return rows
    return _Company


def test_prior_year_sections_returns_risk_and_mda_from_one_filing():
    """Both baselines come from ONE parsed filing - that is what makes the
    similarity free (no second network fetch)."""
    from shortlist.research.filings import _prior_year_sections
    rows = [_FakeFiling("2026-02-01", "2025-12-31"), _FakeFiling("2025-02-01", "2024-12-31")]
    risk, mda = _prior_year_sections("A", company_factory=_fake_company(rows))
    assert risk == "prior risk text"
    assert mda == "prior mda text"
    # Pin the "zero extra network request" invariant mechanically: risk_factors
    # and management_discussion must come off the SAME .obj() call, not two.
    assert rows[1].obj_calls == 1


def test_prior_year_sections_empty_without_a_prior_year():
    from shortlist.research.filings import _prior_year_sections
    rows = [_FakeFiling("2026-02-01", "2025-12-31")]          # only one 10-K
    assert _prior_year_sections("A", company_factory=_fake_company(rows)) == ("", "")


def test_prior_year_sections_never_raises():
    from shortlist.research.filings import _prior_year_sections
    def _boom(_ticker):
        raise RuntimeError("edgar exploded")
    assert _prior_year_sections("A", company_factory=_boom) == ("", "")


def test_similarity_enabled_defaults_on_and_honours_false():
    """enabled: false -> no similarity computed (the byte-identical escape hatch)."""
    from shortlist.research import filings
    cfg = {"research": {"text_similarity": {"enabled": False}}}
    assert filings._similarity_enabled(cfg) is False
    assert filings._similarity_enabled({}) is True          # default ON
