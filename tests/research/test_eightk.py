"""Selection + extraction for recent 8-K substance (research/eightk.py).

Every fixture below is a MEASURED filing shape from the 60-filing probe recorded in
docs/audits/2026-08-13-eightk-text-in-deep-design.md §2.4 — not an invented shape.
The one exception is Item 4.02, which did not appear in the sample at all (§5); it is
tested against a constructed fixture and is explicitly NOT claimed as verified.
"""
from datetime import date

import pytest

from shortlist.research import eightk
from shortlist.research.eightk import _ELISION, config_block, extract, fetch_eightks, select

# The probe ran 2026-08-12/13; every `filed` below is relative to this.
TODAY = date(2026, 8, 13)
CFG = config_block(None)


class _Att:
    """An edgartools attachment: `document_type` + a lazily-parsed `text()`."""

    def __init__(self, document_type, text="", raises=False):
        self.document_type = document_type
        self._text = text
        self._raises = raises

    def text(self):
        if self._raises:
            raise RuntimeError("attachment parse failed")
        return self._text


class _Filing:
    def __init__(self, filed, items="", body="", exhibits=(), form="8-K", accession="acc"):
        self.form = form
        self.filing_date = filed
        self.items = items
        self.exhibits = list(exhibits)
        self.accession_no = accession
        self._body = body

    def text(self):
        return self._body


def _company(filings):
    class _C:
        def __init__(self, ticker):
            self.ticker = ticker

        def get_filings(self, form=None):
            return list(filings)
    return _C


# --------------------------------------------------------------------------- #
# Selection (§3.1)
# --------------------------------------------------------------------------- #
def test_selection_does_not_depend_on_the_filing_events_index():
    """F1: JPM's 40-row MIXED-form index (35 SCHEDULE 13G/A) collapses to 26 days
    (2026-07-16 -> 2026-08-11), leaving its Item 2.02 earnings release of 2026-07-14
    two days outside. A dedicated form="8-K" call must still find it."""
    jpm_2202 = _Filing("2026-07-14", items="2.02,9.01", body="cover",
                       exhibits=[_Att("EX-99.1", "Net revenue rose.")])
    in_window = _Filing("2026-08-11", items="8.01", body="unrelated")
    picked = select([in_window, jpm_2202], CFG, today=TODAY)
    assert [f.filing_date for f, _, _ in picked] == ["2026-07-14"]


def test_priority_ordering_beats_recency_and_recency_breaks_ties():
    """4.02 outranks a fresher 2.02: a non-reliance restatement unconditionally
    stops a thesis. Ties inside one priority rank fall back to newest-first."""
    restatement = _Filing("2026-05-01", items="4.02", body="non-reliance")
    older_2202 = _Filing("2026-06-01", items="2.02", body="q1")
    newer_2202 = _Filing("2026-07-30", items="2.02,9.01", body="q2")
    cfg = {**CFG, "max_filings": 3}
    picked = select([older_2202, newer_2202, restatement], cfg, today=TODAY)
    assert [f.filing_date for f, _, _ in picked] == ["2026-05-01", "2026-07-30", "2026-06-01"]


def test_selection_drops_amendments_non_priority_items_and_stale_filings():
    rows = [
        _Filing("2026-08-01", items="2.02", form="8-K/A", body="amendment"),
        _Filing("2026-08-01", items="7.01,9.01", body="reg-fd only"),
        _Filing("2025-01-01", items="2.02", body="outside lookback"),
        _Filing("2026-08-02", items="2.02", body="keeper"),
    ]
    picked = select(rows, CFG, today=TODAY)
    assert [f.filing_date for f, _, _ in picked] == ["2026-08-02"]


def test_selection_respects_max_filings():
    rows = [_Filing(f"2026-07-{d:02d}", items="2.02", body="b") for d in (10, 11, 12, 13)]
    assert len(select(rows, {**CFG, "max_filings": 2}, today=TODAY)) == 2


# --------------------------------------------------------------------------- #
# Extraction (§3.2)
# --------------------------------------------------------------------------- #
def test_pure_2_02_skips_the_boilerplate_body():
    """AAPL 2026-07-30 (2.02,9.01): the 4,607-char body is pure cover boilerplate;
    the EX-99.1 release carries everything."""
    f = _Filing("2026-07-30", items="2.02,9.01", body="Cover page boilerplate.",
                exhibits=[_Att("EX-99.1", "Apple reported revenue of $94.0 billion.")])
    text, parts = extract(f, ["2.02"], 6000, 1500)
    assert parts == ["EX-99.1"]
    assert "Cover page boilerplate" not in text
    assert "94.0 billion" in text


def test_multi_item_filing_keeps_the_body():
    """F2: NKE 2026-06-23 is 2.02,5.02,7.01 — the EX-99.1 release is 5,114 chars but
    the officer-change narrative lives ONLY in the 17,522-char body."""
    f = _Filing("2026-06-23", items="2.02,5.02,7.01,9.01",
                body="The Board appointed a new Chief Financial Officer.",
                exhibits=[_Att("EX-99.1", "Fourth quarter revenues were flat.")])
    text, parts = extract(f, ["2.02", "5.02"], 6000, 1500)
    assert parts == ["body", "EX-99.1"]
    assert "Chief Financial Officer" in text and "revenues were flat" in text


def test_empty_exhibit_falls_back_to_the_body():
    """F3: JPM 2026-06-25 (Item 5.02) files an EX-99.1 of length 0. A non-value-aware
    rule emits nothing for it."""
    f = _Filing("2026-06-25", items="5.02", body="Departure of a named executive officer.",
                exhibits=[_Att("EX-99.1", "")])
    text, parts = extract(f, ["5.02"], 6000, 1500)
    assert parts == ["body"]
    assert "named executive officer" in text


def test_no_exhibit_at_all_is_body_only():
    """CVX 2026-04-09 is a 2.02 with NO exhibit (14,987-char body); XOM 2026-07-01
    carries EX-3/EX-4 charter exhibits only. Both must degrade to the body."""
    cvx = _Filing("2026-04-09", items="2.02", body="Chevron reported earnings of $3.5 billion.")
    text, parts = extract(cvx, ["2.02"], 6000, 1500)
    assert parts == ["body"] and "3.5 billion" in text

    xom = _Filing("2026-07-01", items="1.01,2.01,3.01,3.03,5.02,5.03,9.01",
                  body="Completion of the acquisition.",
                  exhibits=[_Att("EX-3.1", "Restated certificate."),
                            _Att("EX-4.1", "Indenture.")])
    text, parts = extract(xom, ["2.01", "1.01", "5.02"], 6000, 1500)
    assert parts == ["body"] and "Restated certificate" not in text


def test_lowest_numbered_ex99_wins_and_bare_ex99_sorts_first():
    """JPM 2026-07-14: EX-99.1 is the 38,553-char release, EX-99.2 the 115,377-char
    financial supplement. LLY 2026-04-30 names its release a bare `EX-99`."""
    jpm = _Filing("2026-07-14", items="2.02,9.01", body="cover",
                  exhibits=[_Att("EX-99.2", "Financial supplement tables."),
                            _Att("EX-99.1", "Net income was $18.0 billion.")])
    text, parts = extract(jpm, ["2.02"], 6000, 1500)
    assert parts == ["EX-99.1"] and "Financial supplement" not in text

    lly = _Filing("2026-04-30", items="2.02,9.01", body="cover",
                  exhibits=[_Att("EX-99.2", "Slides."), _Att("EX-99", "Lilly reported.")])
    text, parts = extract(lly, ["2.02"], 6000, 1500)
    assert parts == ["EX-99"] and "Lilly reported" in text


def test_whitespace_is_collapsed_on_ingest():
    """§2.3: collapse recovers 47-85% of exhibit bytes and is free — `assess._norm`
    already collapses whitespace on both sides at verification."""
    f = _Filing("2026-07-30", items="2.02",
                exhibits=[_Att("EX-99.1", "Revenue\n\n   rose\t\t8%.")])
    text, _ = extract(f, ["2.02"], 6000, 1500)
    assert text == "Revenue rose 8%."


def test_an_unreadable_exhibit_does_not_blank_its_siblings():
    f = _Filing("2026-07-14", items="2.02", body="cover",
                exhibits=[_Att("EX-99.1", raises=True), _Att("EX-99.2", "Usable release.")])
    text, parts = extract(f, ["2.02"], 6000, 1500)
    assert parts == ["EX-99.2"] and text == "Usable release."


# --------------------------------------------------------------------------- #
# The guidance splice (§3.2, F4) — the one heuristic in the design
# --------------------------------------------------------------------------- #
def _release_with_outlook_at(frac: float, total: int = 38000) -> str:
    """A release whose only outlook language sits at `frac` of the document, the
    structural pattern measured on JPM (0.45) and CVX (0.41)."""
    marker = "Outlook: we expect full-year revenue to grow."
    head = "x" * int(total * frac)
    return head + marker + "y" * (total - len(head) - len(marker))


@pytest.mark.parametrize("frac", [0.45, 0.41])   # JPM, CVX
def test_guidance_splice_recovers_an_outlook_the_prefix_would_lose(frac):
    text = _release_with_outlook_at(frac)
    cap, window = 6000, 1500
    out = eightk._cap(text, cap, window)
    assert "Outlook: we expect full-year revenue" in out
    assert _ELISION in out
    assert len(out) == cap                       # a TRADE: the window displaces prefix chars
    assert out.startswith(text[:cap - window - len(_ELISION)])
    assert "Outlook" not in text[:cap]           # the plain prefix really did lose it


def test_without_a_late_hit_the_output_is_exactly_the_plain_prefix():
    text = "z" * 38000
    assert eightk._cap(text, 6000, 1500) == text[:6000]
    assert _ELISION not in eightk._cap(text, 6000, 1500)


def test_a_hit_already_inside_the_prefix_does_not_trigger_a_splice():
    text = "We expect revenue to grow. " + "z" * 38000
    assert eightk._cap(text, 6000, 1500) == text[:6000]


def test_splice_is_disabled_by_a_zero_window():
    text = _release_with_outlook_at(0.45)
    assert eightk._cap(text, 6000, 0) == text[:6000]


def test_text_under_the_cap_is_untouched():
    assert eightk._cap("short", 6000, 1500) == "short"


# --------------------------------------------------------------------------- #
# Budget walk (§3.2) + the fetch contract (§3.3)
# --------------------------------------------------------------------------- #
def test_budget_walk_is_deterministic_and_never_exceeds_max_chars_total():
    rows = [_Filing(f"2026-07-{d:02d}", items="2.02", accession=f"a{d}",
                    exhibits=[_Att("EX-99.1", "w" * 20000)]) for d in (10, 11, 12)]
    out = fetch_eightks("X", None, company_factory=_company(rows), today=TODAY)
    # Equal shares, not a priority-ordered greedy walk. The greedy version gave
    # [6000, 4000] and silently DROPPED the third filing — measured on NKE, where
    # that third slot held the Item 5.02 CFO appointment (the one material event in
    # the window) while two routine 2.02 releases ate the budget.
    assert [len(e.text) for e in out] == [3334, 3333, 3333]
    assert sum(len(e.text) for e in out) == CFG["max_chars_total"]
    assert [e.filed for e in out] == ["2026-07-12", "2026-07-11", "2026-07-10"]


def test_label_carries_date_priority_items_and_source_documents():
    f = _Filing("2026-07-30", items="2.02,9.01", body="cover",
                exhibits=[_Att("EX-99.1", "Apple reported revenue.")])
    out = fetch_eightks("AAPL", None, company_factory=_company([f]), today=TODAY)
    # 9.01 is a pointer to the exhibit list, not an event — it is not named.
    assert out[0].label == "8-K 2026-07-30 (Item 2.02, EX-99.1)"
    assert out[0].items == "2.02,9.01"


def test_disabled_block_returns_nothing_and_never_touches_the_network():
    def _boom(ticker):
        raise AssertionError("must not fetch when disabled")
    cfg = {"research": {"eightk": {"enabled": False}}}
    assert fetch_eightks("X", cfg, company_factory=_boom, today=TODAY) == []


def test_absent_block_ships_on():
    """The one research key whose ABSENT block is not a no-op (§3.6, like
    text_similarity) — `enabled: false` is the byte-identical escape hatch."""
    f = _Filing("2026-07-30", items="2.02", exhibits=[_Att("EX-99.1", "Reported.")])
    assert fetch_eightks("X", {}, company_factory=_company([f]), today=TODAY)


def test_a_dead_sec_endpoint_never_raises(capsys):
    def _boom(ticker):
        raise RuntimeError("HTTP 503 from sec.gov")
    assert fetch_eightks("X", None, company_factory=_boom, today=TODAY) == []
    assert "8-K index fetch failed" in capsys.readouterr().err


def test_an_all_empty_filing_is_dropped_rather_than_emitted_blank():
    f = _Filing("2026-07-30", items="2.02", body="", exhibits=[_Att("EX-99.1", "")])
    assert fetch_eightks("X", None, company_factory=_company([f]), today=TODAY) == []


def test_item_4_02_is_selected_first_from_a_constructed_fixture():
    """§5: NO Item 4.02 appeared in the 60-filing sample, so the top of the priority
    table is exercised by construction only. Its first real restatement is its first
    real test — this is NOT evidence the rule works on live 4.02 shapes."""
    rows = [_Filing("2026-08-01", items="2.02", exhibits=[_Att("EX-99.1", "Q2 results.")]),
            _Filing("2026-05-01", items="4.02", body="Non-reliance on previously issued "
                                                     "financial statements.")]
    out = fetch_eightks("X", None, company_factory=_company(rows),
                        today=TODAY)
    assert out[0].label == "8-K 2026-05-01 (Item 4.02, body)"


# ---------------------------------------------------------------------------
# Cover-page stripping and budget allocation. Both pin MEASURED failures found
# on NKE 2026-08-10 (Item 5.02) during the post-build value test — see
# docs/audits/2026-08-13-eightk-text-in-deep-design.md §6.
# ---------------------------------------------------------------------------

_COVER = ("UNITED STATES SECURITIES AND EXCHANGE COMMISSION Washington, D.C. 20549 "
          "FORM 8-K CURRENT REPORT Pursuant to Section 13 or 15(d) of the Securities "
          "Exchange Act of 1934 August 4, 2026 NIKE, Inc. Oregon 1-10635 93-0584541 "
          "One Bowerman Drive Beaverton OR 97005-6453 Registrant's telephone number "
          "Check the appropriate box below if the Form 8-K filing is intended to "
          "simultaneously satisfy the filing obligation of the registrant ")
_SUBSTANCE = ("Item 5.02 Departure of Directors or Certain Officers. David Denton, "
              "the company's newly appointed Executive Vice President and Chief "
              "Financial Officer, effective as of August 17, 2026, will also assume "
              "the role of interim Corporate Controller.")


def test_cover_page_is_stripped_so_the_event_survives_the_cap():
    """NKE 2026-08-10: 4,023-char body, substance at char 2,672, 600-char budget.
    Before the fix this returned pure letterhead."""
    body = _COVER + _SUBSTANCE
    assert len(_COVER) >= 200          # the guard's own precondition
    stripped = eightk._strip_cover(body)
    assert stripped.startswith("Item 5.02")
    assert "Chief Financial Officer" in stripped[:600]
    assert "Bowerman" not in stripped


def test_a_body_with_no_item_heading_is_returned_untouched():
    body = "no heading anywhere in this body at all"
    assert eightk._strip_cover(body) == body


def test_an_implausibly_early_heading_is_not_trusted():
    """A match inside the header itself must not chop the body to nothing."""
    body = "Item 1.01 " + "x" * 500
    assert eightk._strip_cover(body) == body


def test_allocation_gives_a_short_material_filing_its_share():
    """The measured starvation: two long 2.02s must not consume the budget and
    leave the 5.02 with cover-page scraps."""
    grants = eightk._allocate([76803, 83684, 1400], total=10000, per_filing=6000)
    assert grants[2] == 1400, "the short 5.02 must get everything it has"
    assert sum(grants) <= 10000


def test_allocation_redistributes_unused_share_in_priority_order():
    grants = eightk._allocate([500, 90000, 90000], total=9000, per_filing=6000)
    assert grants[0] == 500
    assert grants[1] > grants[2] or grants[1] == 6000
    assert sum(grants) <= 9000


def test_allocation_never_exceeds_either_cap():
    grants = eightk._allocate([99999, 99999], total=10000, per_filing=6000)
    assert sum(grants) <= 10000
    assert all(g <= 6000 for g in grants)


def test_allocation_of_nothing_is_empty():
    assert eightk._allocate([], total=10000, per_filing=6000) == []
