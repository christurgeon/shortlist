"""Debt & liquidity notes reach the prompt, the haystack and the cache key correctly.

The unit rules live in test_debt_notes.py; this file pins the three CROSS-FILE
properties that no single module can enforce:
  1. a note is filing text, so it enters the haystack as its OWN labelled segment
     (CLAUDE.md: /deep grounding is per-segment);
  2. an absent/disabled block leaves the prompt byte-identical;
  3. notes do NOT move the cache key — they come from filings already in it — but
     the extractor's SOURCE does, via cachekey._PROMPT_MODULES.
"""
import pytest

from shortlist.research import filings
from shortlist.research.assess import _build_user_prompt, _verify_grounding
from shortlist.research.cachekey import _PROMPT_MODULES
from shortlist.research.models import (Conflict, DebtNote, FilingBundle, FilingText,
                                       QualitativeAssessment)

LADDER = ("| 2027 | 4,100 | | 2028 | 3,200 | Aggregate maturities of long-term debt "
          "for the next five years are as follows.")


def _note(form="10-K", title="LONG-TERM OBLIGATIONS", text=LADDER, truncated=False):
    return DebtNote(form=form, accession="acc", title=title,
                    label=f"{form} note: {title}", text=text, truncated=truncated)


def _assessment():
    return QualitativeAssessment(ticker="AMT", as_of="2026-08-20", filing_accession="acc",
                                 filing_date="2026-02-20", model="test")


def _bundle(debt_notes=()):
    tenk = FilingText("AMT", "acc", "2026-02-20", business="b", mda="m", risk_factors="r")
    return FilingBundle(tenk=tenk, primary_accession="acc", cache_key="acc",
                        filing_date="2026-02-20", debt_notes=list(debt_notes))


# ------------------------------------------------------------------------ segments

def test_a_note_is_its_own_labelled_haystack_segment():
    labels = dict(_bundle([_note()]).segments())
    assert "10-K note: LONG-TERM OBLIGATIONS" in labels
    assert LADDER in labels["10-K note: LONG-TERM OBLIGATIONS"]


def test_a_note_quote_is_attributed_to_the_note_not_the_10k():
    """The whole point of a per-segment haystack: a verified quote must name the
    note, so 'verified' never silently widens from 'the 10-K'."""
    bundle = _bundle([_note()])
    a = _assessment()
    a.reconciliation = [Conflict(signal="risk", verdict="confirms",
                                 tension="near-term maturities",
                                 filing_says="Aggregate maturities of long-term debt")]
    _verify_grounding(a, bundle)
    assert a.reconciliation[0].verified is True
    assert a.reconciliation[0].source == "10-K note: LONG-TERM OBLIGATIONS"


def test_both_forms_keep_separate_labels():
    labels = dict(_bundle([_note(), _note(form="10-Q", title="Borrowings")]).segments())
    assert "10-K note: LONG-TERM OBLIGATIONS" in labels
    assert "10-Q note: Borrowings" in labels


def test_content_past_the_truncation_cut_cannot_be_quoted():
    """The guarantee truncation actually gives. NOT the 8-K _ELISION property: an
    elision splices two non-adjacent spans, so a quote crossing it asserts a false
    contiguity; truncation only drops a suffix. What holds is that cut text is
    ABSENT from the haystack, so a model reconstructing a severed figure fails."""
    bundle = _bundle([_note(text="maturities due within one year", truncated=True)])
    a = _assessment()
    a.reconciliation = [
        Conflict(signal="risk", verdict="confirms", tension="cut text",
                 filing_says="due within one year total $4,100 million"),
        Conflict(signal="risk", verdict="confirms", tension="shown text",
                 filing_says="maturities due within one year"),
    ]
    _verify_grounding(a, bundle)
    assert a.reconciliation[0].verified is False   # never shown => not quotable
    assert a.reconciliation[1].verified is True    # shown => quotable


def test_no_non_filing_marker_is_quotable_from_a_truncated_note():
    """Regression for a real defect. An earlier revision appended " […truncated…]"
    to the note TEXT; normalized that is 13 chars against assess._MIN_EVIDENCE_CHARS
    = 12, so a model emitting the marker ALONE as evidence got verified=True against
    a real note — non-filing text passing quote-verification, which CLAUDE.md
    forbids. The signal now lives in the prompt header; the segment stays pure
    filing text. A one-character margin was never a safety property."""
    note = _note(text="maturities due within one year", truncated=True)
    assert "truncat" not in note.text.lower()
    for label, seg in _bundle([note]).segments():
        assert "truncat" not in seg.lower(), f"non-filing marker leaked into {label}"


def test_the_truncation_signal_reaches_the_model_via_the_header():
    """It still has to be VISIBLE — SYSTEM_PROMPT tells the model to name a missing
    input rather than estimate one, which it cannot do if a cut ladder looks whole."""
    p = _build_user_prompt(_bundle([_note(truncated=True)]), {"research": {}}, card=None)
    assert "(TRUNCATED" in p
    bare = _build_user_prompt(_bundle([_note(truncated=False)]), {"research": {}}, card=None)
    assert "TRUNCATED" not in bare


# -------------------------------------------------------------------------- prompt

def test_prompt_carries_the_note_under_a_form_and_title_header():
    p = _build_user_prompt(_bundle([_note()]), {"research": {}}, card=None)
    assert "=== 10-K STATEMENT NOTE — LONG-TERM OBLIGATIONS ===" in p
    assert LADDER in p


def test_prompt_is_byte_identical_without_notes():
    bare = _build_user_prompt(_bundle(), {"research": {}}, card=None)
    assert "STATEMENT NOTE" not in bare


def test_disabling_the_block_yields_no_notes_and_so_the_bare_prompt():
    """`enabled: false` must be a no-op, not a differently-shaped prompt."""
    from shortlist.research.notes import collect, config_block
    cfg = config_block({"research": {"notes": {"enabled": False}}})

    class _F:
        notes = None
    assert collect(_F(), "10-K", "acc", "AMT", cfg) == []


# ----------------------------------------------------------------------- cache key

def test_the_extractor_source_is_in_the_prompt_fingerprint():
    """Without this, editing a selection rule or a cap serves stale briefs."""
    assert "notes" in _PROMPT_MODULES


def test_notes_do_not_add_an_accession_to_the_cache_key(monkeypatch):
    """They come from the 10-K/10-Q already in the key, so the key must not move —
    the config block reaches it through _config_fingerprint instead."""
    tenk = FilingText("AMT", "0000123-26-000100", "2026-02-20",
                      business="b", mda="m", risk_factors="r")
    monkeypatch.setattr(filings, "_prior_year_sections", lambda *a, **k: ("", ""))

    import edgar

    from shortlist.research import eightk
    monkeypatch.setattr(eightk, "fetch_eightks", lambda *a, **k: [])
    monkeypatch.setattr(edgar, "Company",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network")))

    class _Idx:
        def __len__(self):
            return 1

        def __getitem__(self, i):
            class _N:
                title = "LONG-TERM OBLIGATIONS"

                def to_markdown(self):
                    return LADDER
            return _N()

    class _Obj:
        notes = _Idx()

    monkeypatch.setattr(filings, "_fetch_10k_parsed", lambda *a, **k: (tenk, _Obj()))
    bundle = filings.fetch_bundle("AMT")
    assert bundle.cache_key == "0000123-26-000100"
    assert [n.title for n in bundle.debt_notes] == ["LONG-TERM OBLIGATIONS"]


# ------------------------------------------------------- edgartools API drift guard

def test_edgartools_note_api_surface_is_still_what_notes_py_reads():
    """`edgar` is pinned only as `edgartools>=3.0`, and this repo has been broken by
    edgartools drift before (a standard_concept rename silently broke accruals).

    The failure mode here is SILENT — if `Note.title` or `Notes.__getitem__` is
    renamed, collect() degrades to zero notes and every brief quietly loses its
    maturity ladder. Fail at CI time instead. Offline: inspects the classes only,
    no network and no filing parse.
    """
    import inspect

    edgar_notes = pytest.importorskip("edgar.xbrl.notes")
    assert hasattr(edgar_notes.Notes, "__len__")
    assert hasattr(edgar_notes.Notes, "__getitem__")
    assert "title" in inspect.signature(edgar_notes.Note.__init__).parameters
    assert callable(getattr(edgar_notes.Note, "to_markdown", None))
