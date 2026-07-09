"""Pure buyback-authorization aggregator (scout/buyback.py): filter order, cross-phrase
accession dedup, resolver abstention, quality drops, emission cik+meta shape."""
from shortlist.scout.buyback import (DEFAULT_PHRASES, SIGNAL, STRENGTH,
                                     buyback_events_from_rows)


def _row(adsh, cik="0000000007", phrase="approved a new share repurchase program",
         file_date="2026-06-25", file_type="8-K", items=("8.01",), sics=("3571",),
         names=("Real Business Inc",)):
    return {"adsh": adsh, "cik": cik, "phrase": phrase, "file_date": file_date,
            "file_type": file_type, "items": list(items), "sics": list(sics),
            "display_names": list(names)}


def _resolve(mapping):
    return lambda cik: mapping.get(cik)


def test_default_phrase_set_shape():
    assert "approved a new share repurchase program" in DEFAULT_PHRASES
    assert all(isinstance(p, str) for p in DEFAULT_PHRASES)


def test_emits_with_cik_and_meta():
    rows = [_row("a-1")]
    ems = buyback_events_from_rows(rows, resolve_ticker_fn=_resolve({"0000000007": "RBI"}))
    assert len(ems) == 1
    e = ems[0]
    assert e.ticker == "RBI" and e.signal == SIGNAL and e.strength == STRENGTH
    assert e.is_discovery is True and e.cik == "0000000007"
    assert e.meta == {"adsh": "a-1", "items": ["8.01"], "file_date": "2026-06-25",
                      "phrase": "approved a new share repurchase program"}
    assert "approved a new share repurchase program" in e.evidence


def test_cross_phrase_accession_dedup():
    """The same accession matched under two phrases -> ONE emission (first phrase wins)."""
    rows = [_row("dup", phrase="approved a share repurchase program"),
            _row("dup", phrase="authorized a share repurchase program")]
    ems = buyback_events_from_rows(rows, resolve_ticker_fn=_resolve({"0000000007": "RBI"}))
    assert len(ems) == 1
    assert ems[0].meta["phrase"] == "approved a share repurchase program"


def test_amendment_row_dropped_first():
    rows = [_row("a-1", file_type="8-K/A")]
    ems = buyback_events_from_rows(rows, resolve_ticker_fn=_resolve({"0000000007": "RBI"}))
    assert ems == []


def test_no_ticker_dropped_no_display_names_fallback():
    rows = [_row("a-1", cik="0000000099", names=["Some Real Company Inc"])]
    ems = buyback_events_from_rows(rows, resolve_ticker_fn=_resolve({}))   # unresolvable
    assert ems == []


def test_spac_sic_and_name_dropped():
    sic_row = _row("a-1", sics=["6770"])
    name_row = _row("a-2", cik="0000000008", sics=["3571"],
                    names=["Blank Check Acquisition Corp"])
    ems = buyback_events_from_rows([sic_row, name_row],
                                   resolve_ticker_fn=_resolve({"0000000007": "RBI",
                                                               "0000000008": "SPAK"}))
    assert ems == []


def test_deny_list_and_junk_suffix_dropped():
    rows = [_row("a-1", cik="0000000007"), _row("a-2", cik="0000000008")]
    ems = buyback_events_from_rows(
        rows, resolve_ticker_fn=_resolve({"0000000007": "DENY", "0000000008": "ABCDF"}),
        deny_list=["deny"])
    # DENY dropped by deny_list; ABCDF dropped by the 5th-letter *F suffix rule
    assert ems == []


def test_per_ticker_per_day_dedup_first_wins():
    """Two DISTINCT same-day accessions for one issuer -> ONE emission (mirrors eightk.py's
    final per-ticker-per-day dedup), so a double-file day never burns two daily_cap slots."""
    rows = [_row("a-1", cik="0000000007", file_date="2026-06-25"),
            _row("a-2", cik="0000000007", file_date="2026-06-25")]   # same ticker + day
    ems = buyback_events_from_rows(rows, resolve_ticker_fn=_resolve({"0000000007": "RBI"}))
    assert [e.ticker for e in ems] == ["RBI"]           # deduped to one
    assert ems[0].meta["adsh"] == "a-1"                 # FIRST accession wins
    # the SUPPRESSED sibling is attached to the winner so the signal can persist it as seen
    assert ems[0].meta["sibling_adsh"] == ["a-2"]
    # a different DAY for the same ticker is NOT deduped (a genuine re-authorization)
    rows2 = [_row("a-1", cik="0000000007", file_date="2026-06-25"),
             _row("a-3", cik="0000000007", file_date="2026-07-10")]
    ems2 = buyback_events_from_rows(rows2, resolve_ticker_fn=_resolve({"0000000007": "RBI"}))
    assert [e.meta["adsh"] for e in ems2] == ["a-1", "a-3"]


def test_drop_spacs_false_keeps_named_shell():
    rows = [_row("a-1", names=["Something Acquisition Corp"])]
    ems = buyback_events_from_rows(rows, resolve_ticker_fn=_resolve({"0000000007": "RBI"}),
                                   drop_spacs=False)
    assert [e.ticker for e in ems] == ["RBI"]   # SIC still checked, but name check disabled
