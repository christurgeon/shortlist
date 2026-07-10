"""Negative-item veto: ScoutState veto-map/cursor/ledger round-trips + the
daily._negative_veto_sweep run step (mirrors test_scout_daily_firehose.py's tmp-state
idiom; EFTS + resolver are monkeypatched at module level — no network)."""
from datetime import date

import shortlist.data.efts as efts_mod
import shortlist.scout.cik_tickers as ct_mod
import shortlist.scout.daily as daily_mod
import shortlist.scout.notify as notify_mod
import shortlist.screen as screen_mod
from shortlist.models import ScoreCard
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


def test_sweep_cap_truncation_marks_only_recorded_logged(tmp_path, monkeypatch):
    """FIX: a firehose max_events_per_run cap must not silently mark ALL fresh matches as
    logged — only the ones the firehose layer actually persisted. The N-K events cut by
    the cap must stay OUT of eightk_neg_logged so they remain "fresh" and are retried
    (re-attempt firehose logging) on the next sweep, instead of being permanently and
    wrongly written off as already-recorded."""
    st = ScoutState(tmp_path / "s.json")
    cfg = {"eightk": {"negative_veto": {"enabled": True, "lookback_days": 30}},
           "firehose": {"enabled": True, "max_events_per_run": 1}}
    rows = [_row("n-1", cik="0000000001", items=("1.03",)),
            _row("n-2", cik="0000000002", items=("2.06",)),
            _row("n-3", cik="0000000003", items=("4.02",))]
    _patch_window(monkeypatch, lambda start, end, **kw: rows)
    _patch_resolver(monkeypatch, {"0000000001": "AAA", "0000000002": "BBB", "0000000003": "CCC"})

    _negative_veto_sweep(st, cfg, date(2026, 7, 6))
    logged = st.eightk_neg_logged()
    assert len(logged) == 1                                # only the capped-in event marked
    evs = st.firehose_events(on=date(2026, 7, 6), lookback_days=30)
    neg = [e for e in evs if e["signal"] == "edgar:8k_negative"]
    assert len(neg) == 1
    assert neg[0]["meta"]["adsh"] == logged[0]              # the marked one IS the recorded one

    # The two events cut by the cap are still "fresh" on later sweeps (re-inject the same
    # rows to simulate the lag-window re-sweep overlap) -> at cap=1/sweep it takes two more
    # sweeps for the remaining two to each get their turn recorded+marked.
    _negative_veto_sweep(st, cfg, date(2026, 7, 7))
    _negative_veto_sweep(st, cfg, date(2026, 7, 8))
    assert set(st.eightk_neg_logged()) == {"n-1", "n-2", "n-3"}


def test_sweep_firehose_write_failure_marks_nothing_logged(tmp_path, monkeypatch):
    """FIX: if the firehose write itself raises (e.g. a corrupt state file), NONE of this
    sweep's fresh matches may be marked logged — an unrecorded event must retry next
    sweep, never be silently and permanently marked as recorded. The sweep itself must
    still never crash and the veto map/protection must be unaffected."""
    st = ScoutState(tmp_path / "s.json")
    cfg = {"eightk": {"negative_veto": {"enabled": True, "lookback_days": 30}},
           "firehose": {"enabled": True}}
    _patch_window(monkeypatch, lambda start, end, **kw: [_row("n-1", items=("1.03",))])
    _patch_resolver(monkeypatch, {"0000000007": "RBI"})

    def boom(*a, **k):
        raise RuntimeError("state file corrupt")
    monkeypatch.setattr(daily_mod, "cohort_events_from_emissions", boom)

    veto_map, notes = _negative_veto_sweep(st, cfg, date(2026, 7, 6))
    assert st.eightk_neg_logged() == []                     # nothing recorded -> nothing marked
    assert st.firehose_events(on=date(2026, 7, 6), lookback_days=30) == []
    assert "RBI" in veto_map                                # veto protection is unaffected
    assert notes == []                                      # sweep itself doesn't crash/loudly fail


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


# --- run()-level byte-identical proof (absent block vs explicitly-disabled block) ---

class _StubDiscoverySignal:
    """Minimal discovery signal (mirrors test_orchestrator_integration.py's stub)."""
    name = "stub_discovery"
    is_discovery = True

    def scan(self, session: date) -> list[Emission]:
        return [Emission("AAPL", "stub:discovery", 0.8, "test emission", is_discovery=True)]

    def available(self) -> tuple[bool, str]:
        return (True, "1 hit")


class _FakeNotifier:
    def configured(self): return False
    def send_photo(self, *a): return True
    def send_document(self, *a): return True
    def send_message(self, *a): return True


def _make_card(ticker: str) -> ScoreCard:
    return ScoreCard(
        ticker=ticker, composite=75.0, quality=70.0, moat=65.0, growth=80.0,
        momentum=60.0, value=55.0, opportunity=60.0, insider=50.0, gates=[])


def _minimal_run_config(tag: str, tmp_path, eightk_block: dict | None) -> dict:
    cfg = {
        "scout": {
            "state_path": str(tmp_path / tag / "state.json"),
            "artifact_dir": str(tmp_path / tag / "scout"),
            "daily_x": 15,
            "cooldown_days": 7,
            "deep_screen_sources": ["mock"],
            "research_top_n": 0,
            "research_phase_budget_s": 1,
            "daily_push": {"enabled": True, "research": False},
            "signals": {"stub_discovery": {"enabled": True, "weight": 1.0}},
        },
        "scoring": {},
        "gates": {},
    }
    if eightk_block is not None:
        cfg["scout"]["eightk"] = eightk_block
    return cfg


def _run_and_read_artifacts(tag: str, tmp_path, monkeypatch, eightk_block: dict | None) -> tuple[int, dict]:
    cfg = _minimal_run_config(tag, tmp_path, eightk_block)
    rc = daily_mod.run(cfg, demo=False, today=date(2026, 5, 29))  # a Friday (trading day)
    out_dirs = list((tmp_path / tag / "scout").iterdir())
    assert len(out_dirs) == 1, "exactly one session artifact dir expected"
    out_dir = out_dirs[0]
    artifacts = {
        "manifest.json": (out_dir / "manifest.json").read_text(),
        "report.txt": (out_dir / "report.txt").read_text(),
        "report.html": (out_dir / "report.html").read_text(),
    }
    return rc, artifacts


def test_run_explicitly_disabled_byte_identical_to_absent_zero_fetches(tmp_path, monkeypatch):
    """The reviewer-requested gap-close: prove the byte-identical guarantee at the FULL
    daily.run() level, not just at the _negative_veto_sweep unit layer. An explicitly
    disabled `scout.eightk.negative_veto.enabled: false` must produce the exact same
    manifest/report artifacts as an absent `eightk` block -- and neither run may touch
    EFTS or the CIK resolver even once (mirrors test_orchestrator_integration.py's
    stub-signal run() harness)."""
    # run() reads the repo-relative scout/validate-latest.json (VALIDATE_LATEST_PATH) for
    # the display-only validation section — isolate the CWD so the artifacts never depend
    # on live repo state (the test_scout_backfill_cli.py idiom; PR #117 cwd-leak class).
    monkeypatch.chdir(tmp_path)

    def boom(*a, **k):
        raise AssertionError("run() must not fetch EFTS/resolver when the veto is off")
    monkeypatch.setattr(efts_mod, "fetch_eightk_window", boom)
    monkeypatch.setattr(ct_mod, "load_cik_to_ticker", boom)

    def fake_build_signals(names, kwargs_by_name=None):
        return [_StubDiscoverySignal()]
    monkeypatch.setattr(daily_mod, "build_signals", fake_build_signals)

    def fake_run_harness(tickers, sources, config, macro=None):
        return [_make_card(t) for t in tickers]
    monkeypatch.setattr(screen_mod, "run_harness", fake_run_harness)

    monkeypatch.setattr(notify_mod, "TelegramNotifier", lambda: _FakeNotifier())

    rc_absent, artifacts_absent = _run_and_read_artifacts("absent", tmp_path, monkeypatch, None)
    rc_disabled, artifacts_disabled = _run_and_read_artifacts(
        "disabled", tmp_path, monkeypatch, {"negative_veto": {"enabled": False}})

    assert rc_absent == 0 and rc_disabled == 0
    assert artifacts_absent == artifacts_disabled
    # Sanity: the veto actually ran the funnel with an empty map both times (0 vetoed),
    # so this isn't vacuously true because nothing reached the veto stage.
    assert '"vetoed": 0' in artifacts_absent["manifest.json"]
