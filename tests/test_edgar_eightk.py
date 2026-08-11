"""Pure-aggregator tests for scout/eightk.py (mirrors tests/test_scout_edgar_activist.py
style: dict-row fixtures, no network)."""

from shortlist.edgar.eightk import (DEFAULT_ITEM_SETS, NEGATIVE_ITEMS, SIGNAL,
                                    _junk_suffix, eightk_events_from_rows,
                                    match_item_sets, match_negative,
                                    negative_events_from_rows)
from shortlist.bot.models import Emission


def _row(adsh, cik="0000000007", items=("1.01", "3.03"), file_date="2026-07-03",
         file_type="8-K", sics=("3571",), names=("Real Business Inc  (RBI)",)):
    return {"adsh": adsh, "cik": cik, "items": list(items), "file_date": file_date,
            "file_type": file_type, "sics": list(sics), "display_names": list(names)}


def _resolver(mapping):
    return lambda cik: mapping.get(cik)


R7 = _resolver({"0000000007": "RBI"})


def test_match_item_sets_and_semantics():
    assert match_item_sets(("1.01", "3.03", "9.01"), [("1.01", "3.03")]) == ["1.01", "3.03"]
    assert match_item_sets(("1.01", "9.01"), [("1.01", "3.03")]) is None   # AND, not OR
    assert match_item_sets((), [("1.01", "3.03")]) is None
    # multiple sets = OR across sets
    assert match_item_sets(("2.01",), [("1.01", "3.03"), ("2.01",)]) == ["2.01"]


def test_match_negative():
    assert match_negative(("2.06", "9.01")) == ["2.06"]
    assert match_negative(("1.03", "2.06")) == ["1.03", "2.06"]
    assert match_negative(("1.01", "9.01")) is None
    assert frozenset({"1.03", "2.04", "2.05", "2.06",
                                        "3.01", "4.02", "5.01"}) == NEGATIVE_ITEMS


def test_happy_path_emission_shape():
    ems = eightk_events_from_rows([_row("a-1")], resolve_ticker_fn=R7)
    assert len(ems) == 1
    e = ems[0]
    assert e.ticker == "RBI" and e.signal == SIGNAL and e.is_discovery is True
    assert e.cik == "0000000007"
    assert e.meta == {"adsh": "a-1", "items": ["1.01", "3.03"], "file_date": "2026-07-03"}
    assert "1.01" in e.evidence and "2026-07-03" in e.evidence


def test_amendment_dropped_first():
    """forms=8-K returns 8-K/A rows (live-verified) — the file_type drop is mandatory,
    even for a row that matches every other criterion."""
    ems = eightk_events_from_rows([_row("a-1", file_type="8-K/A")], resolve_ticker_fn=R7)
    assert ems == []


def test_item_and_set_default_is_101_and_303():
    assert DEFAULT_ITEM_SETS == (("1.01", "3.03"),)
    ems = eightk_events_from_rows([_row("a-1", items=("1.01", "9.01"))],
                                  resolve_ticker_fn=R7)
    assert ems == []                                   # 1.01 alone does not qualify


def test_sic_6770_blank_check_dropped():
    ems = eightk_events_from_rows([_row("a-1", sics=("6770",))], resolve_ticker_fn=R7)
    assert ems == []


def test_spac_name_dropped_but_never_used_for_ticker():
    spac = _row("a-1", names=("Peace Acquisition Corp  (PECE)",))
    assert eightk_events_from_rows([spac], resolve_ticker_fn=R7) == []
    # drop_spacs=False keeps it — proving the drop was the name check, nothing else
    assert len(eightk_events_from_rows([spac], resolve_ticker_fn=R7,
                                       drop_spacs=False)) == 1


def test_resolver_abstention_no_display_names_fallback():
    """CIK unresolvable -> NO emission, even though display_names carries '(RBI)'."""
    ems = eightk_events_from_rows([_row("a-1")], resolve_ticker_fn=_resolver({}))
    assert ems == []


def test_junk_suffix_and_deny_list_dropped():
    # The deny-list ticker is deliberately NOT 5-letter-suffixed: it used to be `DENYX`,
    # which the `X` (mutual-fund) suffix rule now drops on its own, so the assertion would
    # have passed without the deny list doing any work at all.
    r_f = _row("a-1", cik="1"); r_ok = _row("a-2", cik="2"); r_deny = _row("a-3", cik="3")
    resolver = _resolver({"1": "ABCDF", "2": "GOOD", "3": "DENY"})
    ems = eightk_events_from_rows([r_f, r_ok, r_deny], resolve_ticker_fn=resolver,
                                  deny_list=["deny"])
    assert [e.ticker for e in ems] == ["GOOD"]


def test_mutual_fund_x_suffix_is_a_junk_suffix():
    """Nasdaq's 5th-letter `X` marks an open-end fund. Adding it is provably neutral for
    every committed cohort verdict: `_junk_suffix` is reached only by `_assemble_8k` and
    `_assemble_buyback`, whose cohorts hold 0 X-suffix events across 1,843 + 588 rows
    (docs/audits/2026-08-07-funnel-gate-mismatch.md §3.2)."""
    assert _junk_suffix("BBASX") and _junk_suffix("FTECX") and _junk_suffix("VFLEX")
    assert not _junk_suffix("GOOGL")   # ordinary 5-letter common stock
    assert not _junk_suffix("ONEX")    # 4 chars — rule is 5-letter-only


def test_four_char_ticker_ending_in_suffix_letter_kept():
    ems = eightk_events_from_rows([_row("a-1", cik="9")],
                                  resolve_ticker_fn=_resolver({"9": "WOOF"}))
    assert [e.ticker for e in ems] == ["WOOF"]


def test_accession_dedup_and_per_ticker_per_day_dedup():
    rows = [_row("a-1"), _row("a-1"),                       # duplicated accession
            _row("a-2"),                                    # same ticker, same day
            _row("a-3", file_date="2026-07-04")]            # same ticker, NEXT day
    ems = eightk_events_from_rows(rows, resolve_ticker_fn=R7)
    assert [(e.ticker, e.meta["file_date"]) for e in ems] == [
        ("RBI", "2026-07-03"), ("RBI", "2026-07-04")]


def test_negative_events_extraction():
    rows = [_row("n-1", items=("2.06", "9.01")),
            _row("n-1", items=("2.06", "9.01")),            # accession dedup
            _row("n-2", items=("2.06",), file_type="8-K/A"),  # amendment dropped
            _row("n-3", items=("1.01", "3.03")),            # not negative
            _row("n-4", cik="0000000099", items=("1.03",))]  # unresolvable -> abstain
    out = negative_events_from_rows(rows, resolve_ticker_fn=R7)
    assert out == [{"ticker": "RBI", "cik": "0000000007", "adsh": "n-1",
                    "file_date": "2026-07-03", "items": ["2.06"]}]


def test_negative_extraction_keeps_spacs_and_sic6770():
    """The veto is broad by design — no quality drops (a SPAC bankruptcy still vetoes)."""
    rows = [_row("n-1", items=("1.03",), sics=("6770",),
                 names=("Blank Check Acquisition Corp",))]
    out = negative_events_from_rows(rows, resolve_ticker_fn=R7)
    assert len(out) == 1 and out[0]["items"] == ["1.03"]


# --- Emission.meta plumbing (models.py + firehose.py) ---

def test_emission_meta_defaults_empty_and_is_positional_back_compat():
    e = Emission("ABC", "edgar:activist_13d", 0.9, "ev", True, "0000123")
    assert e.meta == {} and e.cik == "0000123"

