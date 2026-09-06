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


def test_tenq_mda_does_not_fall_back_to_bare_item_2(capsys):
    """INTC (measured 2026-08-14): get_item_with_part('Part I','Item 2') returns 0 chars
    because edgartools misses the Part I Item 2 heading. `tenq["Item 2"]` is NOT a safe
    recovery — on INTC it returns 2,459 chars of *Part II* Item 2 (unregistered sales /
    share repurchases), which would be silently labelled MD&A in the grounding haystack.
    Abstaining is correct; this test exists so the "obvious fix" is not re-added."""
    from shortlist.research.filings import _tenq_mda

    part_ii_item_2 = ("Issuer Purchases of Equity Securities. During the quarter we "
                      "repurchased shares under the publicly announced program. " * 20)

    class _FakeINTC:
        def get_item_with_part(self, part, item, markdown=True):
            return ""                      # Part I Item 2 heading undetected
        def __getitem__(self, key):
            assert key == "Item 2"
            return part_ii_item_2          # wrong content: Part II Item 2

    assert _tenq_mda(_FakeINTC(), "INTC") == ""
    assert "repurchased shares" not in capsys.readouterr().err


def test_tenq_mda_ignores_the_items_list(capsys):
    """XOM / TSLA / MCD (measured 2026-08-14): `tenq.items` is NOT a usable guard. XOM
    lists an unqualified 'Item 2', TSLA lists three entries, MCD exactly one — yet
    get_item_with_part('Part I','Item 2') returns 69,820 / 49,879 / 122,045 chars for
    them. Any guard keyed on `items` reports phantom failures, so a misleading or empty
    `items` must not suppress a working extraction."""
    from shortlist.research.filings import _tenq_mda

    mda = "Management's Discussion and Analysis. Revenue rose on volume. " * 500

    class _FakeItemsMismatch:
        def __init__(self, items):
            self.items = items
        def get_item_with_part(self, part, item, markdown=True):
            assert (part, item) == ("Part I", "Item 2")
            return mda

    for items in ([], ["Item 2"], ["Item 1", "Item 2", "Item 3"], ["Part I Item 2"]):
        assert _tenq_mda(_FakeItemsMismatch(items), "XOM") == mda
    assert capsys.readouterr().err == ""


def test_tenq_mda_logs_the_abstention(capsys):
    """The INTC 0-char gap went unnoticed because _tenq_mda returned "" silently."""
    from shortlist.research.filings import _tenq_mda

    class _Empty:
        def get_item_with_part(self, *a, **k):
            return ""

    assert _tenq_mda(_Empty(), "INTC") == ""
    err = capsys.readouterr().err
    assert "INTC" in err and "10-Q MD&A" in err


def test_tenq_mda_logs_extraction_failure_and_never_raises(capsys):
    from shortlist.research.filings import _tenq_mda

    class _Boom:
        def get_item_with_part(self, *a, **k):
            raise ValueError("item boundary detection failed")

    assert _tenq_mda(_Boom(), "AAPL") == ""
    err = capsys.readouterr().err
    assert "AAPL" in err and "ValueError" in err


def test_tenq_mda_notes_over_capture_without_changing_the_text(capsys):
    """JPM 0.846 / MCD 0.644 / PFE 0.566 of the whole 10-Q (measured 2026-08-14) vs a
    median 0.230 and p90 0.397 for normal names. Over-capture is NOT harmful — the span
    starts at a genuine MD&A heading and the prefix surviving the 40K cap is real MD&A
    prose — so it must be observable only, never truncated or abstained."""
    from shortlist.research.filings import _tenq_mda

    mda = "x" * 8460

    class _FakeJPM:
        doc = type("_Doc", (), {"text": staticmethod(lambda: "y" * 10000)})()
        def get_item_with_part(self, *a, **k):
            return mda

    assert _tenq_mda(_FakeJPM(), "JPM") == mda           # unchanged
    err = capsys.readouterr().err
    assert "JPM" in err and "over-captured" in err and "0.85" in err


def test_tenq_mda_no_over_capture_note_for_a_normal_span(capsys):
    from shortlist.research.filings import _tenq_mda

    class _FakeNormal:
        doc = type("_Doc", (), {"text": staticmethod(lambda: "y" * 10000)})()
        def get_item_with_part(self, *a, **k):
            return "x" * 2300                            # 0.230, the measured median

    assert _tenq_mda(_FakeNormal(), "KO") == "x" * 2300
    assert capsys.readouterr().err == ""


def test_tenq_mda_over_capture_check_is_skipped_when_doc_is_unusable(capsys):
    """Whole-document length is neither cheap nor guaranteed; a missing or raising
    `doc` must skip the check silently and never change the returned text."""
    from shortlist.research.filings import _tenq_mda

    class _NoDoc:
        def get_item_with_part(self, *a, **k):
            return "mda text"

    class _RaisingDoc:
        @property
        def doc(self):
            raise RuntimeError("html fetch failed")
        def get_item_with_part(self, *a, **k):
            return "mda text"

    for fake in (_NoDoc(), _RaisingDoc()):
        assert _tenq_mda(fake, "X") == "mda text"
        assert capsys.readouterr().err == ""


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


def test_prior_year_sections_reuses_a_given_filings_list_without_fetching():
    """When `filings` is passed, _prior_year_sections must not call the company
    factory at all — that's the whole point of reuse."""
    from shortlist.research.filings import _prior_year_sections
    rows = [_FakeFiling("2026-02-01", "2025-12-31"), _FakeFiling("2025-02-01", "2024-12-31")]

    def _boom(_ticker):
        raise AssertionError("company_factory must not be called when filings= is given")

    risk, mda = _prior_year_sections("A", company_factory=_boom, filings=rows)
    assert (risk, mda) == ("prior risk text", "prior mda text")


def test_fetch_10k_parsed_returns_the_filings_index_as_a_fourth_element(monkeypatch):
    """fetch_bundle needs the raw filings-index object (not just `.latest(1)`) so
    it can hand the SAME list to _prior_year_sections instead of re-fetching it."""
    import shortlist.research.filings as filings_mod
    from shortlist.research.filings import _fetch_10k_parsed

    class _Filings(list):
        def latest(self, n):
            return self[0] if self else None

    class _Filing:
        accession_no = "acc-1"
        filing_date = "2026-01-01"
        def obj(self):
            return _FakeTenK(business="b", mda="m", risk="r")

    filings_index = _Filings([_Filing()])

    class _FakeCompany:
        def __init__(self, ticker):
            pass
        def get_filings(self, form):
            return filings_index

    import edgar
    monkeypatch.setattr(edgar, "Company", _FakeCompany)
    monkeypatch.setattr(filings_mod, "require_identity", lambda *a, **k: None)

    _filing, _obj, _latest, returned_filings = _fetch_10k_parsed("X", "me@x.com")

    assert returned_filings is filings_index


def test_fetch_bundle_fetches_the_10k_filings_index_only_once(monkeypatch):
    """_prior_year_sections used to re-fetch the 10-K filings index from scratch to
    find the prior fiscal year, costing one extra SEC request per brief. fetch_bundle
    must fetch it once and hand the SAME list to both _fetch_10k_parsed and
    _prior_year_sections."""
    from shortlist.research import eightk, filings as filings_mod

    class _CurTenK:
        business, management_discussion, risk_factors = "b", "m", "r"

    class _PriorTenK:
        business, management_discussion, risk_factors = "pb", "pm", "pr"

    class _CurFiling:
        form = "10-K"
        accession_no = "acc-cur"
        filing_date = "2026-08-01"
        period_of_report = "2026-06-30"
        def obj(self):
            return _CurTenK()

    class _PriorFiling:
        form = "10-K"
        accession_no = "acc-prior"
        filing_date = "2025-08-01"
        period_of_report = "2025-06-30"
        def obj(self):
            return _PriorTenK()

    rows = [_CurFiling(), _PriorFiling()]

    class _TenKFilings(list):
        def latest(self, n):
            return self[0]

    class _Counts:
        ten_k = 0

    class _FakeCompany:
        def __init__(self, ticker):
            pass
        def get_filings(self, form):
            if form == "10-K":
                _Counts.ten_k += 1
                return _TenKFilings(rows)
            return []   # no 10-Q -> fetch_bundle's own try/except degrades to ""

    monkeypatch.setattr(filings_mod, "require_identity", lambda *a, **k: None)
    monkeypatch.setattr(eightk, "fetch_eightks", lambda *a, **k: [])
    import edgar
    monkeypatch.setattr(edgar, "Company", _FakeCompany)

    cfg = {"research": {"notes": {"enabled": False}, "controls": {"enabled": False},
                         "text_similarity": {"enabled": False}}}
    bundle = filings_mod.fetch_bundle("X", config=cfg)

    assert bundle is not None
    assert _Counts.ten_k == 1


def test_similarity_enabled_defaults_on_and_honours_false():
    """enabled: false -> no similarity computed (the byte-identical escape hatch)."""
    from shortlist.research import filings
    cfg = {"research": {"text_similarity": {"enabled": False}}}
    assert filings._similarity_enabled(cfg) is False
    assert filings._similarity_enabled({}) is True          # default ON


def test_tenq_risk_factors_uses_part_ii_item_1a():
    """TenQ has no `risk_factors` attribute (verified live, 10/10 names, TODO.md
    §2a) — its risk factors live at Part II Item 1A, the same item _tenq_added_risks
    already reads for the /deep diff."""
    from shortlist.research.filings import _tenq_risk_factors

    class _FakeTenQ:
        def get_item_with_part(self, part, item, markdown=True):
            assert (part, item) == ("Part II", "Item 1A")
            return "Quarterly risk factors text."

    assert _tenq_risk_factors(_FakeTenQ()) == "Quarterly risk factors text."


def test_tenq_risk_factors_empty_on_missing():
    from shortlist.research.filings import _tenq_risk_factors

    class _Bare:
        def get_item_with_part(self, *a, **k):
            return None

    assert _tenq_risk_factors(_Bare()) == ""
    assert _tenq_risk_factors(object()) == ""          # no method at all -> ""


def test_tenq_risk_factors_never_raises(capsys):
    from shortlist.research.filings import _tenq_risk_factors

    class _Boom:
        def get_item_with_part(self, *a, **k):
            raise ValueError("item boundary detection failed")

    assert _tenq_risk_factors(_Boom(), "AAPL") == ""
    err = capsys.readouterr().err
    assert "AAPL" in err and "ValueError" in err


def test_filing_sections_10q_routes_risk_through_part_ii_item_1a():
    """The regression this task fixes: filing_text_change(form="10-Q") used to read
    `_section(obj, "risk_factors")`, which is always "" on a TenQ."""
    from shortlist.research.filings import _filing_sections

    class _FakeTenQ:
        risk_factors = None            # TenQ genuinely has no such attribute in prod
        def get_item_with_part(self, part, item, markdown=True):
            if (part, item) == ("Part II", "Item 1A"):
                return "New risk factors this quarter."
            if (part, item) == ("Part I", "Item 2"):
                return "Quarterly MD&A."
            return None

    risk, mda = _filing_sections(_FakeTenQ(), "10-Q", "X")
    assert risk == "New risk factors this quarter."
    assert mda == "Quarterly MD&A."


def test_filing_sections_10k_stays_on_the_risk_factors_property():
    """10-K path must stay byte-identical: risk factors come from the `risk_factors`
    attribute, and get_item_with_part is never called for it."""
    from shortlist.research.filings import _filing_sections

    class _FakeTenK:
        risk_factors = "10-K risk factors."
        management_discussion = "10-K MD&A."
        def get_item_with_part(self, *a, **k):
            raise AssertionError("10-K risk factors must not call get_item_with_part")

    risk, mda = _filing_sections(_FakeTenK(), "10-K", "X")
    assert risk == "10-K risk factors."
    assert mda == "10-K MD&A."


def test_filing_sections_10q_risk_empty_when_extractor_raises(capsys):
    """An exception (or empty return) from get_item_with_part abstains to "" rather
    than raising or falling back to a wrong-Part item."""
    from shortlist.research.filings import _filing_sections

    class _Boom:
        def get_item_with_part(self, *a, **k):
            raise RuntimeError("boom")

    risk, mda = _filing_sections(_Boom(), "10-Q", "X")
    assert risk == "" and mda == ""


class _FormFiling:
    """A filings-index row with a settable form — the amendment cases turn on it."""
    def __init__(self, form, accession, filing_date, tenk=None):
        self.form, self.accession_no, self.filing_date = form, accession, filing_date
        self._tenk = tenk or _FakeTenK(business="b", mda="m", risk="r")
        self.obj_calls = 0

    def obj(self):
        self.obj_calls += 1
        return self._tenk


class _FormFilings(list):
    def latest(self, n):
        return self[0] if self else None


def _patch_edgar_index(monkeypatch, rows):
    """Install a fake edgar.Company whose get_filings returns `rows`, newest-first
    exactly as edgartools orders them."""
    import shortlist.research.filings as filings_mod

    index = _FormFilings(rows)

    class _FakeCompany:
        def __init__(self, ticker):
            pass

        def get_filings(self, form):
            return index

    import edgar
    monkeypatch.setattr(edgar, "Company", _FakeCompany)
    monkeypatch.setattr(filings_mod, "require_identity", lambda *a, **k: None)
    return index


def test_fetch_10k_parsed_skips_a_newer_amendment(monkeypatch):
    """A 10-K/A is usually a Part III patch with no Item 1/1A/7, and edgartools
    returns it inside form="10-K". Taking `.latest(1)` blindly picked it and the
    brief died with "no 10-K" (TSLA, 2026-04-30 accession 0001104659-26-053166) or,
    worse, ran on a half-empty document (AMD: business=0, risk=0, mda present)."""
    from shortlist.research.filings import _fetch_10k_parsed

    amendment = _FormFiling("10-K/A", "acc-amend", "2026-04-30",
                            _FakeTenK(business=None, mda=None, risk=None))
    original = _FormFiling("10-K", "acc-10k", "2026-01-29")
    _patch_edgar_index(monkeypatch, [amendment, original])

    filing, tenk, latest, _index = _fetch_10k_parsed("TSLA", "me@x.com")

    assert filing is not None
    assert filing.accession == "acc-10k"
    assert filing.filing_date == "2026-01-29"
    assert latest is original
    assert tenk is original._tenk
    assert amendment.obj_calls == 0        # never parse the amendment


def test_fetch_10k_parsed_uses_an_amendment_when_no_exact_10k_exists(monkeypatch):
    """Preferring the exact form must not strand a filer whose index holds only
    amendments — there is nothing else to fall back to."""
    from shortlist.research.filings import _fetch_10k_parsed

    amendment = _FormFiling("10-K/A", "acc-amend", "2026-04-30")
    _patch_edgar_index(monkeypatch, [amendment])

    filing, _tenk, latest, _index = _fetch_10k_parsed("X", "me@x.com")

    assert latest is amendment
    assert filing is not None and filing.accession == "acc-amend"


def test_fetch_10k_parsed_picks_the_newest_10k_regardless_of_index_order(monkeypatch):
    """Selection is by filing_date, not by position — same convention as
    _prior_year_sections, which already sorts rather than trusting the index."""
    from shortlist.research.filings import _fetch_10k_parsed

    older = _FormFiling("10-K", "acc-2025", "2025-01-30")
    newer = _FormFiling("10-K", "acc-2026", "2026-01-29")
    _patch_edgar_index(monkeypatch, [older, newer])

    filing, _tenk, _latest, _index = _fetch_10k_parsed("X", "me@x.com")

    assert filing.accession == "acc-2026"


def test_fetch_10k_parsed_keeps_a_falsy_10k_instead_of_the_amendment(monkeypatch):
    """Selection must test for None, not truthiness. edgartools' EntityFiling
    defines no __bool__/__len__ today, but a filing row that ever became falsy
    would silently hand the brief back to the amendment this function exists to
    avoid — the same quiet-degradation shape as the AMD case."""
    from shortlist.research.filings import _fetch_10k_parsed

    class _FalsyFiling(_FormFiling):
        def __len__(self):
            return 0

    amendment = _FormFiling("10-K/A", "acc-amend", "2026-04-30")
    original = _FalsyFiling("10-K", "acc-10k", "2026-01-29")
    _patch_edgar_index(monkeypatch, [amendment, original])

    _filing, _tenk, latest, _index = _fetch_10k_parsed("X", "me@x.com")

    assert latest is original
