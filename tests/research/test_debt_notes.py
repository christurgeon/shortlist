"""Selection + extraction for debt & liquidity notes (research/notes.py).

Every fixture below is a MEASURED note shape from the 20-filing probe recorded in
docs/audits/2026-08-20-debt-liquidity-notes-design.md §3 — titles are quoted as
filed, and the two selection traps (AMT's `LONG-TERM OBLIGATIONS`, DUK's
`Investments in Debt and Equity Securities`) are the reason those rules exist.
"""
import pytest

from shortlist.research import notes as dn
from shortlist.research.notes import collect, config_block, extract, select

CFG = config_block(None)


class _Note:
    """An edgartools note: a `title` plus a lazily-rendered `to_markdown()`."""

    def __init__(self, title, markdown="body", raises=False):
        self.title = title
        self._md = markdown
        self._raises = raises

    def to_markdown(self):
        if self._raises:
            raise RuntimeError("note render failed")
        return self._md


class _Notes:
    """edgar.xbrl.notes.Notes: len() + integer indexing."""

    def __init__(self, items, raises=False):
        self._items = list(items)
        self._raises = raises

    def __len__(self):
        if self._raises:
            raise RuntimeError("notes index unavailable")
        return len(self._items)

    def __getitem__(self, i):
        return self._items[i]


class _Filing:
    def __init__(self, notes):
        self.notes = notes


def _titles(selected):
    return [n.title for n in selected]


# --------------------------------------------------------------------------- select

@pytest.mark.parametrize("title", [
    "Debt",                                    # AAPL
    "DEBT",                                    # MSFT / UAL
    "Long-term Debt",                          # JPM
    "Borrowings",                              # LLY / INTC
    "SHORT-TERM BORROWINGS AND CREDIT LINES",  # NKE
    "Borrowings and Credit Agreements",        # CVS
    "Credit Agreement",                        # MRNA
    "Credit Facilities and Commercial Paper",  # O
    "Notes Payable",                           # O
    "DEBT AND COMMITMENTS",                    # F
    "Unsecured Borrowings",                    # GS
    "Commercial Paper and Long-Term Debt",     # TGT
    "LONG-TERM OBLIGATIONS",                   # AMT — the only `obligation` match
])
def test_select_matches_every_measured_debt_note_title(title):
    assert _titles(select(_Notes([_Note(title)]), CFG)) == [title]


def test_select_excludes_duk_investments_in_debt_asset_note():
    """DUK files an ASSET note whose title contains `debt`. Unfiltered it was
    selected and consumed 10,127 chars of budget (design §3.1)."""
    idx = _Notes([_Note("Debt and Credit Facilities"),
                  _Note("Investments in Debt and Equity Securities")])
    assert _titles(select(idx, CFG)) == ["Debt and Credit Facilities"]


def test_select_does_not_match_asset_retirement_obligations():
    """The `long-term obligation` rule must stay narrow: bare `obligation` would
    also match AMT's own ASSET RETIREMENT OBLIGATIONS, which is not debt."""
    assert select(_Notes([_Note("ASSET RETIREMENT OBLIGATIONS")]), CFG) == []


@pytest.mark.parametrize("title", [
    "Marketable Securities",
    "Investments in Unconsolidated Affiliates",
    "LEASES",
    "Income Taxes",
])
def test_select_ignores_unrelated_notes(title):
    assert select(_Notes([_Note(title)]), CFG) == []


def test_select_keeps_both_notes_when_a_filer_splits_them():
    """NKE and O both legitimately file two relevant notes (design §3.1)."""
    idx = _Notes([_Note("SHORT-TERM BORROWINGS AND CREDIT LINES"),
                  _Note("LONG-TERM DEBT")])
    assert _titles(select(idx, CFG)) == ["SHORT-TERM BORROWINGS AND CREDIT LINES",
                                         "LONG-TERM DEBT"]


def test_select_is_uncapped_so_collect_can_count_emitted_notes():
    """The cap moved to `collect`. Capping candidates here let an empty-rendering
    note eat a slot a real note behind it wanted (measured: 3 candidates, cap 2,
    first renders empty -> 1 note emitted instead of 2)."""
    idx = _Notes([_Note("Debt"), _Note("Borrowings"), _Note("Credit Agreement")])
    assert _titles(select(idx, {**CFG, "max_notes_per_form": 2})) == [
        "Debt", "Borrowings", "Credit Agreement"]


def test_select_returns_empty_when_no_note_matches():
    """JPM/XOM/LLY/T/CVS file no debt note in their latest 10-Q at all — a
    legitimate subset of the annual disclosure, not a parse failure."""
    idx = _Notes([_Note("Income Taxes"), _Note("Revenue"), _Note("LEASES")])
    assert select(idx, CFG) == []


def test_select_tolerates_a_note_with_no_title():
    """The probe measured an untitled leading note on AMT and DUK."""
    assert _titles(select(_Notes([_Note(""), _Note("Debt")]), CFG)) == ["Debt"]


# -------------------------------------------------------------------------- extract

def test_extract_collapses_whitespace():
    """UAL's raw note is 20,836 chars and 8,259 collapsed (0.40) — collapse before
    any cap, or the cap spends its budget on padding."""
    text, truncated = extract(_Note("DEBT", "a   b\t\tc\n\n\n\nd"), 1000)
    assert text == "a b c\n\nd"
    assert truncated is False


def test_extract_leaves_a_short_note_untouched_and_unmarked():
    text, truncated = extract(_Note("Debt", "short note"), 1000)
    assert text == "short note"
    assert truncated is False


def test_extract_truncates_at_a_whitespace_boundary_within_the_limit():
    body = "word " * 100                      # 500 chars
    text, truncated = extract(_Note("Debt", body), 50)
    assert truncated is True
    assert len(text) <= 50                    # never longer than the limit
    assert not text.endswith(" ")             # boundary trimmed, not left dangling
    assert text.split()[-1] == "word"         # never a partial token


def test_extract_puts_no_marker_in_the_text():
    """The truncation signal is a PROMPT HEADER concern. `text` is a grounding
    segment, so a marker inside it would be non-filing text a model could quote and
    have verified — the defect an earlier revision shipped."""
    text, truncated = extract(_Note("Debt", "word " * 100), 50)
    assert truncated is True
    assert "truncat" not in text.lower() and "[" not in text


def test_extract_drops_rather_than_severs_when_there_is_no_safe_cut():
    """No whitespace inside the limit => no cut that preserves whole tokens. Emitting
    `4,100,` from `4,100,000,000` would hand a prompt told to do arithmetic a figure
    that is wrong by three orders of magnitude."""
    text, truncated = extract(_Note("Debt", "4,100,000,000,123,456,789"), 6)
    assert (text, truncated) == ("", True)


def test_extract_never_severs_a_number_mid_digits():
    """The tables ARE the payload; a cut through `4,100` -> `4,1` would present a
    wrong figure to a prompt that is explicitly asked to do arithmetic."""
    body = "| 2029 | 4,100 | 3,200 |"
    for limit in range(8, len(body)):
        payload, _ = extract(_Note("Debt", body), limit)
        for token in ("4,100", "3,200"):
            head = body[:body.index(token)]
            if head in payload and len(payload) > len(head):
                assert token in payload, f"severed {token} at limit={limit}"


def test_extract_returns_empty_for_an_empty_note():
    assert extract(_Note("Debt", "   \n\n  "), 100) == ("", False)


# -------------------------------------------------------------------------- collect

def test_collect_builds_labelled_notes_for_the_form():
    filing = _Filing(_Notes([_Note("LONG-TERM OBLIGATIONS", "ladder")]))
    out = collect(filing, "10-K", "acc-1", "AMT", CFG)
    assert len(out) == 1
    assert out[0].form == "10-K"
    assert out[0].accession == "acc-1"
    assert out[0].title == "LONG-TERM OBLIGATIONS"
    assert out[0].text == "ladder"
    assert out[0].label == "10-K note: LONG-TERM OBLIGATIONS"
    assert out[0].truncated is False


def test_collect_enforces_the_per_form_total_budget():
    """A shared pool would let a heavy borrower's annual notes crowd out the
    fresher quarterly note; the budget is per form (design §3.3)."""
    cfg = {**CFG, "max_chars_per_note": 100, "max_chars_10k": 120,
           "max_notes_per_form": 2}
    filing = _Filing(_Notes([_Note("Debt", "a " * 60), _Note("Borrowings", "b " * 60)]))
    out = collect(filing, "10-K", "acc", "T", cfg)
    assert sum(len(n.text) for n in out) <= 120


def test_collect_drops_a_note_when_the_budget_is_exhausted():
    cfg = {**CFG, "max_chars_per_note": 100, "max_chars_10k": 100,
           "max_notes_per_form": 2}
    filing = _Filing(_Notes([_Note("Debt", "a " * 80), _Note("Borrowings", "b " * 80)]))
    out = collect(filing, "10-K", "acc", "T", cfg)
    assert _titles(out) == ["Debt"]


def test_collect_is_a_no_op_when_disabled():
    filing = _Filing(_Notes([_Note("Debt", "ladder")]))
    assert collect(filing, "10-K", "acc", "T", {**CFG, "enabled": False}) == []


def test_collect_never_raises_when_the_notes_index_fails(capsys):
    filing = _Filing(_Notes([], raises=True))
    assert collect(filing, "10-K", "acc", "T", CFG) == []
    assert "research:" in capsys.readouterr().err


def test_a_filing_with_no_notes_attribute_is_reported_not_silent(capsys):
    """The drift trap. edgartools is pinned only `>=3.0`; if `.notes` is renamed,
    silence would make a systematic failure look exactly like 'this filer has no
    debt note' on every single brief."""
    class _Bare:
        pass
    assert collect(_Bare(), "10-K", "acc", "T", CFG) == []
    assert "no `.notes`" in capsys.readouterr().err


def test_no_parsed_filing_is_silent_because_that_is_expected(capsys):
    """Distinct from the case above: no filing parsed is a normal degraded path
    (fetch_bundle passes None), not a signal worth printing."""
    assert collect(None, "10-K", "acc", "T", CFG) == []
    assert capsys.readouterr().err == ""


def test_collect_degrades_only_the_note_that_fails_to_render(capsys):
    filing = _Filing(_Notes([_Note("Debt", raises=True), _Note("Borrowings", "ok")]))
    out = collect(filing, "10-K", "acc", "T", CFG)
    assert _titles(out) == ["Borrowings"]
    assert "research:" in capsys.readouterr().err


def test_collect_skips_a_note_that_renders_empty():
    filing = _Filing(_Notes([_Note("Debt", "   "), _Note("Borrowings", "ok")]))
    assert _titles(collect(filing, "10-K", "acc", "T", CFG)) == ["Borrowings"]


def test_collect_uses_the_10q_budget_for_a_10q():
    cfg = {**CFG, "max_chars_per_note": 10000, "max_chars_10k": 10000,
           "max_chars_10q": 20}
    filing = _Filing(_Notes([_Note("Debt", "x " * 100)]))
    out = collect(filing, "10-Q", "acc", "T", cfg)
    assert out[0].truncated is True
    assert len(out[0].text) <= 20


# --------------------------------------------------------------------------- config

def test_config_block_defaults_are_on():
    assert config_block(None)["enabled"] is True
    assert config_block({})["max_chars_per_note"] == 16000
    assert config_block({})["max_chars_10k"] == 16000
    assert config_block({})["max_chars_10q"] == 8000


def test_config_block_merges_over_defaults():
    cfg = config_block({"research": {"notes": {"max_notes_per_form": 5}}})
    assert cfg["max_notes_per_form"] == 5
    assert cfg["enabled"] is True


def test_per_note_cap_is_16k_because_duk_needs_it():
    """The 12-month ladder sits within the first 10,000 chars in 8 of 9 over-cap
    notes; DUK's is at 13,022. A 10,000 cap would silently drop the ladder for a
    utility while appearing to work (design §3.3)."""
    assert dn._DEFAULTS["max_chars_per_note"] >= 13022


# ----------------------------------------------- exception boundary (adversarial review)

def test_a_notes_property_that_raises_does_not_escape_collect(capsys):
    """`.notes` is a `cached_property` whose body reaches `self.financials` and does
    a LIVE XBRL download + parse, so an HTTP error surfaces on ATTRIBUTE ACCESS.
    `getattr(obj, "notes", None)` only swallows AttributeError. An escape here does
    not cost the notes — the 10-K collect call in fetch_bundle is outside every try,
    so it discards the WHOLE brief for a name whose narrative parsed perfectly."""
    class _Exploding:
        @property
        def notes(self):
            raise ValueError("xbrl instance is malformed")

    assert collect(_Exploding(), "10-K", "acc", "T", CFG) == []
    assert "research:" in capsys.readouterr().err


def test_a_malformed_config_value_does_not_escape_collect(capsys):
    """An operator typo in a knob the config comments invite tuning must degrade,
    not take out every ticker in the run with a generic 'filing error'."""
    filing = _Filing(_Notes([_Note("Debt", "ladder")]))
    bad = {**CFG, "max_chars_10k": "16k"}
    assert collect(filing, "10-K", "acc", "T", bad) == []
    assert "research:" in capsys.readouterr().err


def test_an_empty_rendering_candidate_does_not_consume_a_note_slot():
    """The cap counts EMITTED notes. With the cap on candidates instead, the empty
    first note ate a slot and this filer got one note where two were available."""
    cfg = {**CFG, "max_notes_per_form": 2}
    filing = _Filing(_Notes([_Note("Debt", "   "),
                             _Note("Borrowings", "ok"),
                             _Note("Credit Agreement", "also ok")]))
    assert _titles(collect(filing, "10-K", "acc", "T", cfg)) == [
        "Borrowings", "Credit Agreement"]
