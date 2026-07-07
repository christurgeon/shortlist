"""Negative-item veto: ScoutState veto-map/cursor/ledger round-trips + the
daily._negative_veto_sweep run step (mirrors test_scout_daily_firehose.py's tmp-state
idiom; EFTS + resolver are monkeypatched at module level — no network)."""
from datetime import date

import shortlist.data.efts as efts_mod
import shortlist.scout.cik_tickers as ct_mod
from shortlist.scout.daily import _negative_veto_sweep, _veto_notes
from shortlist.scout.models import Candidate, Emission
from shortlist.scout.state import ScoutState


def _row(adsh, cik="0000000007", items=("2.06",), file_date="2026-07-03",
         file_type="8-K"):
    return {"adsh": adsh, "cik": cik, "items": list(items), "file_date": file_date,
            "file_type": file_type, "sics": ["3571"],
            "display_names": ["Real Business Inc"]}


def _patch_window(monkeypatch, fn):
    monkeypatch.setattr(efts_mod, "fetch_eightk_window", fn)


def _patch_resolver(monkeypatch, mapping):
    monkeypatch.setattr(ct_mod, "load_cik_to_ticker", lambda identity, **kw: mapping)


_CFG_ON = {"eightk": {"negative_veto": {"enabled": True, "lookback_days": 30}}}


# --- ScoutState round-trips ---

def test_state_negative_map_merge_newest_wins_and_upcases(tmp_path):
    st = ScoutState(tmp_path / "s.json")
    assert st.eightk_negative_map() == {}                 # absent key: back-compat
    st.update_eightk_negative(
        [{"ticker": "rbi", "adsh": "n-1", "file_date": "2026-07-01", "items": ["2.06"]},
         {"ticker": "RBI", "adsh": "n-2", "file_date": "2026-07-03", "items": ["1.03"]}],
        swept_through="2026-07-04", on=date(2026, 7, 6))
    m = ScoutState(tmp_path / "s.json").eightk_negative_map()
    assert m == {"RBI": {"last_date": "2026-07-03", "items": ["1.03"], "adsh": "n-2"}}


def test_state_negative_map_pruned_on_write_and_cursor_monotonic(tmp_path):
    st = ScoutState(tmp_path / "s.json")
    st.update_eightk_negative(
        [{"ticker": "OLD", "adsh": "o-1", "file_date": "2026-06-01", "items": ["2.05"]}],
        swept_through="2026-06-02", on=date(2026, 6, 3))
    st.update_eightk_negative(
        [{"ticker": "NEW", "adsh": "n-1", "file_date": "2026-07-05", "items": ["1.03"]}],
        swept_through="2026-07-04", on=date(2026, 7, 6))
    m = ScoutState(tmp_path / "s.json").eightk_negative_map()
    assert set(m) == {"NEW"}                              # 35d-old entry pruned at 30d lookback
    st.update_eightk_negative([], swept_through="2026-07-01", on=date(2026, 7, 6))
    assert st.eightk_negative_swept_through() == "2026-07-04"   # cursor never regresses


def test_state_veto_note_ledger_and_prune(tmp_path):
    st = ScoutState(tmp_path / "s.json")
    st.update_eightk_negative(
        [{"ticker": "RBI", "adsh": "n-1", "file_date": "2026-07-03", "items": ["2.06"]}],
        swept_through="2026-07-04", on=date(2026, 7, 6))
    assert st.eightk_veto_note_seen("RBI", "n-1") is False
    st.mark_eightk_veto_noted("RBI", "n-1")
    assert ScoutState(tmp_path / "s.json").eightk_veto_note_seen("RBI", "n-1") is True
    # A NEWER accession replaces the map entry -> the old pair is pruned from the ledger
    # and the new accession notes afresh (a new negative event deserves a new note).
    st.update_eightk_negative(
        [{"ticker": "RBI", "adsh": "n-2", "file_date": "2026-07-05", "items": ["1.03"]}],
        swept_through="2026-07-05", on=date(2026, 7, 7))
    assert st.eightk_veto_note_seen("RBI", "n-1") is False
    assert st.eightk_veto_note_seen("RBI", "n-2") is False


def test_state_neg_logged_capped_round_trip(tmp_path):
    st = ScoutState(tmp_path / "s.json")
    assert st.eightk_neg_logged() == []                   # absent key: back-compat
    st.add_eightk_neg_logged(["n-1", "n-2"])
    st.add_eightk_neg_logged(["n-2", "n-3"])              # idempotent on repeats
    assert ScoutState(tmp_path / "s.json").eightk_neg_logged() == ["n-1", "n-2", "n-3"]
    st.add_eightk_neg_logged([f"x-{i}" for i in range(600)], cap=500)
    kept = st.eightk_neg_logged()
    assert len(kept) == 500 and "n-1" not in kept and "x-599" in kept


# --- the sweep run step ---

def test_sweep_absent_or_disabled_zero_fetches_empty(tmp_path, monkeypatch):
    """The byte-identical guarantee at the sweep layer: absent/disabled block -> ({}, [])
    and NOT ONE EFTS or resolver call."""
    def boom(*a, **k):
        raise AssertionError("sweep must not fetch when the veto is off")
    _patch_window(monkeypatch, boom)
    monkeypatch.setattr(ct_mod, "load_cik_to_ticker", boom)
    st = ScoutState(tmp_path / "s.json")
    assert _negative_veto_sweep(st, {}, date(2026, 7, 6)) == ({}, [])
    off = {"eightk": {"negative_veto": {"enabled": False}}}
    assert _negative_veto_sweep(st, off, date(2026, 7, 6)) == ({}, [])


def test_sweep_cold_start_bounded_window_and_map(tmp_path, monkeypatch):
    st = ScoutState(tmp_path / "s.json")
    seen = {}

    def fake_window(start, end, **kw):
        seen["span"] = (start, end)
        return [_row("n-1")]

    _patch_window(monkeypatch, fake_window)
    _patch_resolver(monkeypatch, {"0000000007": "RBI"})
    veto_map, notes = _negative_veto_sweep(st, _CFG_ON, date(2026, 7, 6))
    assert seen["span"] == (date(2026, 6, 7), date(2026, 7, 6))  # bounded: 30 days incl session
    assert notes == []
    assert veto_map == {"RBI": {"last_date": "2026-07-03", "items": ["2.06"], "adsh": "n-1"}}
    # cursor lags EFTS_LAG_DAYS so the young days are re-swept until final
    assert st.eightk_negative_swept_through() == "2026-07-04"


def test_sweep_resumes_from_cursor(tmp_path, monkeypatch):
    st = ScoutState(tmp_path / "s.json")
    st.update_eightk_negative([], swept_through="2026-07-03", on=date(2026, 7, 5))
    seen = {}

    def fake_window(start, end, **kw):
        seen["span"] = (start, end)
        return []

    _patch_window(monkeypatch, fake_window)
    _patch_resolver(monkeypatch, {})
    _negative_veto_sweep(st, _CFG_ON, date(2026, 7, 6))
    assert seen["span"] == (date(2026, 7, 4), date(2026, 7, 6))  # cursor+1 .. session


def test_sweep_failure_stale_map_and_loud_note(tmp_path, monkeypatch):
    st = ScoutState(tmp_path / "s.json")
    st.update_eightk_negative(
        [{"ticker": "OLD", "adsh": "o-1", "file_date": "2026-07-01", "items": ["1.03"]}],
        swept_through="2026-07-02", on=date(2026, 7, 4))
    _patch_window(monkeypatch, lambda start, end, **kw: None)    # EFTS down
    _patch_resolver(monkeypatch, {})
    veto_map, notes = _negative_veto_sweep(st, _CFG_ON, date(2026, 7, 6))
    assert "OLD" in veto_map                              # stale protection still applied
    assert len(notes) == 1
    assert "STALE" in notes[0] and "2026-07-02" in notes[0]
    assert st.eightk_negative_swept_through() == "2026-07-02"    # cursor NOT advanced


def test_sweep_firehose_logs_edgar_8k_negative_once_across_runs(tmp_path, monkeypatch):
    """Every match logs to the firehose as its OWN signal, accession-deduped: the re-swept
    lag-window days must not re-log the same filing under a later session."""
    st = ScoutState(tmp_path / "s.json")
    cfg = {"eightk": {"negative_veto": {"enabled": True, "lookback_days": 30}},
           "firehose": {"enabled": True}}
    _patch_window(monkeypatch, lambda start, end, **kw: [_row("n-1", items=("1.03",))])
    _patch_resolver(monkeypatch, {"0000000007": "RBI"})
    _negative_veto_sweep(st, cfg, date(2026, 7, 6))
    _negative_veto_sweep(st, cfg, date(2026, 7, 7))       # lag window re-swept, same row
    evs = st.firehose_events(on=date(2026, 7, 7), lookback_days=30)
    neg = [e for e in evs if e["signal"] == "edgar:8k_negative"]
    assert len(neg) == 1
    assert neg[0]["ticker"] == "RBI" and neg[0]["origin"] == "live"
    assert neg[0]["meta"]["adsh"] == "n-1"                # Emission.meta rode through (Task 2)


def test_veto_notes_named_and_deduped_by_ticker_accession(tmp_path):
    st = ScoutState(tmp_path / "s.json")
    veto_map = {"RBI": {"last_date": "2026-07-03", "items": ["2.06"], "adsh": "n-1"}}
    c = Candidate(ticker="RBI")
    c.add(Emission("RBI", "edgar:form4_cluster_buy", 0.9, "ev", True), 1.0)
    assert _veto_notes(st, [c], veto_map) == \
        ["VETOED: RBI — 8-K item 2.06 filed 2026-07-03"]
    assert _veto_notes(st, [c], veto_map) == []           # same accession: note ONCE, veto daily
    veto_map["RBI"] = {"last_date": "2026-07-05", "items": ["1.03"], "adsh": "n-2"}
    assert _veto_notes(st, [c], veto_map) == \
        ["VETOED: RBI — 8-K item 1.03 filed 2026-07-05"]  # new accession notes afresh
