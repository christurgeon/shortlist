"""8-K backfill legs (Task 5): assemble_eightk_events purity, the generic run_backfill
spec-table coordinator, the disk preflight, and CLI routing. Mirrors
tests/test_scout_backfill.py's injected-seam idiom — no network. NB: the 8k prereg YAMLs
land in Task 6, so every runner call here injects `_prereg` (or monkeypatches load_prereg)."""
from datetime import date, timedelta

import pytest

from shortlist.backtest.prices import PriceHistory
from shortlist.scout import daily
from shortlist.scout.backfill import (assemble_eightk_events, free_disk_gb,
                                      load_backfill_events, run_backfill_8k,
                                      run_backfill_8k_neg, run_backfill_13d)
from shortlist.scout.daily import build_arg_parser
from shortlist.scout.eightk import NEGATIVE_SIGNAL, SIGNAL

TODAY = date(2026, 7, 7)


def _row(adsh, cik="0000000007", items=("1.01", "3.03"), file_date="2023-10-13",
         file_type="8-K", sics=("3571",), names=("Real Business Inc",)):
    """One normalized EFTS row (the Task-1 data/efts.py shape)."""
    return {"adsh": adsh, "cik": cik, "items": list(items), "file_date": file_date,
            "file_type": file_type, "sics": list(sics), "display_names": list(names)}


def _resolve(mapping):
    return lambda cik, as_of: mapping.get(cik)


R7 = _resolve({"0000000007": "RBI"})


def _hist(ticker, start, n_days, base=100.0):
    dates, closes = [], []
    d, i = start, 0
    while len(dates) < n_days:
        if d.weekday() < 5:
            dates.append(d)
            closes.append(base + i * 0.1)
            i += 1
        d = d + timedelta(days=1)
    return PriceHistory(ticker=ticker, dates=dates, closes=closes,
                        nominal_closes=list(closes))


# --- assemble_eightk_events (pure) ---

def test_assemble_happy_path_shape_key_entry_and_sic():
    evs = assemble_eightk_events([_row("a-1")], R7, signal=SIGNAL)
    assert len(evs) == 1
    e = evs[0]
    assert e.signal == SIGNAL and e.ticker == "RBI" and e.cik == "0000000007"
    assert e.origin == "backfill" and e.strength == 0.6
    assert e.event_date == date(2023, 10, 16)             # F12: Fri filing -> Mon entry
    assert e.meta["filing_date"] == "2023-10-13"
    assert e.meta["key"] == f"{SIGNAL}|0000000007|2023-10-13"
    assert e.meta["adsh"] == "a-1"
    assert e.meta["items"] == ["1.01", "3.03"]
    assert e.meta["sic"] == "3571"                        # EFTS sic reused — no fetch later


def test_assemble_excludes_amendment_and_item_mismatch():
    rows = [_row("a-1", file_type="8-K/A"),               # root_forms leak: excluded FIRST
            _row("a-2", items=("1.01", "9.01"))]          # 1.01 alone: AND-set unmet
    assert assemble_eightk_events(rows, R7, signal=SIGNAL) == []


def test_assemble_positive_quality_drops_are_exclusions():
    """Signal-definition filters EXCLUDE (not sentinel): SIC-6770, SPAC name, junk suffix
    — mirroring the LIVE aggregator so the cohort measures what would actually ship."""
    rows = [_row("a-1", cik="1", sics=("6770",)),
            _row("a-2", cik="2", names=("Peace Acquisition Corp",)),
            _row("a-3", cik="3")]
    resolver = _resolve({"1": "AAA", "2": "BBB", "3": "ABCDF"})   # 5th-letter F suffix
    assert assemble_eightk_events(rows, resolver, signal=SIGNAL) == []


def test_assemble_negative_leg_broad_no_quality_drops():
    """The veto cohort is BROAD by design — a SPAC bankruptcy with a junk-suffixed ticker
    still belongs in the negative cohort (matches the live negative_events_from_rows)."""
    rows = [_row("n-1", items=("1.03",), sics=("6770",),
                 names=("Blank Check Acquisition Corp",))]
    evs = assemble_eightk_events(rows, _resolve({"0000000007": "ABCDF"}),
                                 signal=NEGATIVE_SIGNAL, negative=True)
    assert len(evs) == 1
    assert evs[0].signal == NEGATIVE_SIGNAL and evs[0].ticker == "ABCDF"
    assert evs[0].meta["items"] == ["1.03"]
    assert evs[0].meta["key"] == f"{NEGATIVE_SIGNAL}|0000000007|2023-10-13"


def test_assemble_unresolved_is_sentinel_selected_not_dropped():
    evs = assemble_eightk_events([_row("a-1")], _resolve({}), signal=SIGNAL)
    assert len(evs) == 1
    assert evs[0].ticker == "CIK:0000000007"
    assert evs[0].meta["non_measurable_hint"] == "unresolved_ticker"
    assert evs[0].meta["key"] == f"{SIGNAL}|0000000007|2023-10-13"


def test_assemble_resolver_called_with_filing_date_not_entry():
    seen = []

    def resolver(cik, as_of):
        seen.append(as_of)
        return "RBI"
    assemble_eightk_events([_row("a-1")], resolver, signal=SIGNAL)
    assert seen == [date(2023, 10, 13)]                   # PiT at FILING date (F12 guard)


def test_assemble_dedup_accession_and_per_filer_day():
    rows = [_row("a-1"), _row("a-1"),                     # duplicated accession
            _row("a-2"),                                  # same filer, same day
            _row("a-3", file_date="2023-10-16")]          # same filer, next session
    evs = assemble_eightk_events(rows, R7, signal=SIGNAL)
    assert [(e.ticker, e.meta["filing_date"]) for e in evs] == [
        ("RBI", "2023-10-13"), ("RBI", "2023-10-16")]


# --- run_backfill via the spec table ---

class _FakeSym:
    low_confidence: list = []
    disagreements: list = []

    def resolve_ticker(self, cik, as_of):
        return {"0000000007": "RBI", "0000000099": "OTHR"}.get(cik)

    def close(self):
        pass


def test_run_backfill_8k_end_to_end_month_chunks_and_sic_from_rows(tmp_path):
    out = str(tmp_path / "8k.jsonl")
    windows = []

    def fake_window(start, end, identity, **kw):
        windows.append((start, end))
        if start != date(2023, 6, 1):
            return []
        return [_row("a-1", file_date="2023-06-01"),                       # sic inline
                _row("a-2", cik="0000000099", file_date="2023-06-01", sics=()),  # no sic
                _row("a-3", cik="0000000123", file_date="2023-06-01")]     # unresolvable

    sic_calls: list = []

    def fake_sic(cik):
        sic_calls.append(cik)
        return None

    h = _hist("X", date(2022, 7, 1), 500)                 # spans well past 2023-09 (K=3m)

    cfg = {"scout": {"backfill": {"out_dir": str(tmp_path), "sec_throttle_s": 0.0,
                                  "yahoo_throttle_s": 0.0, "score_events": True}}}
    summary = run_backfill_8k(cfg, start=date(2023, 6, 1), end=date(2023, 7, 15),
                              identity="t@example.com", today=TODAY, out_path=out,
                              _fetch_window=fake_window, _symbology=_FakeSym(),
                              _fetch_history=lambda t: h if t in ("RBI", "OTHR", "SPY") else None,
                              _fetch_delisting=lambda cik: [],
                              _fetch_facts=lambda cik: None,   # facts-less: score stays None
                              _fetch_sic=fake_sic,
                              _prereg={"k_months": 3}, _free_gb=lambda p: 50.0)
    assert (date(2023, 6, 1), date(2023, 6, 30)) in windows    # month chunking preserved
    assert (date(2023, 7, 1), date(2023, 7, 15)) in windows
    # sic-from-rows: only the row WITHOUT an inline sic triggered a submissions fetch
    assert sic_calls == ["0000000099"]
    assert summary["n_sic_missing"] == 1                       # OTHR's fetch returned None
    rows = load_backfill_events(out)
    by_ticker = {r["ticker"]: r for r in rows}
    assert set(by_ticker) == {"RBI", "OTHR", "CIK:0000000123"}
    assert by_ticker["RBI"]["meta"]["sic"] == "3571"
    assert by_ticker["RBI"]["meta"]["measurable"] is True      # K=3m matured vs TODAY
    assert by_ticker["CIK:0000000123"]["meta"]["measurable"] is False
    assert summary["n_selected"] == 3 and summary["n_measurable"] == 2
    assert summary["out_path"] == out


def test_run_backfill_loads_prereg_by_signal_slug(monkeypatch, tmp_path):
    seen = []

    def fake_load(slug, *, repo_root):
        seen.append(slug)
        return {"k_months": 3}

    monkeypatch.setattr("shortlist.scout.preregister.load_prereg", fake_load)
    kw = dict(start=date(2023, 6, 1), end=date(2023, 6, 30), identity="t@example.com",
              today=TODAY, _fetch_window=lambda *a, **k: [], _symbology=_FakeSym(),
              _fetch_history=lambda t: None, _fetch_delisting=lambda c: [],
              _free_gb=lambda p: 50.0)
    cfg = {"scout": {"backfill": {"sec_throttle_s": 0.0, "yahoo_throttle_s": 0.0}}}
    run_backfill_8k(cfg, out_path=str(tmp_path / "a.jsonl"), **kw)
    run_backfill_8k_neg(cfg, out_path=str(tmp_path / "b.jsonl"), **kw)
    assert seen == ["edgar_8k", "edgar_8k_negative"]


def test_run_backfill_default_out_paths_are_per_signal(tmp_path):
    kw = dict(start=date(2023, 6, 1), end=date(2023, 6, 30), identity="t@example.com",
              today=TODAY, _fetch_window=lambda *a, **k: [], _symbology=_FakeSym(),
              _fetch_history=lambda t: None, _fetch_delisting=lambda c: [],
              _prereg={"k_months": 3}, _free_gb=lambda p: 50.0)
    cfg = {"scout": {"backfill": {"out_dir": str(tmp_path), "sec_throttle_s": 0.0,
                                  "yahoo_throttle_s": 0.0}}}
    s1 = run_backfill_8k(cfg, **kw)
    s2 = run_backfill_8k_neg(cfg, **kw)
    assert s1["out_path"].endswith("8k-2023-06-01-2023-06-30.jsonl")
    assert s2["out_path"].endswith("8k-neg-2023-06-01-2023-06-30.jsonl")


def test_disk_preflight_aborts_below_floor_before_any_fetch(tmp_path):
    kw = dict(start=date(2023, 6, 1), end=date(2023, 6, 30), identity="t@example.com",
              today=TODAY, out_path=str(tmp_path / "x.jsonl"),
              _fetch_window=lambda *a, **k: (_ for _ in ()).throw(
                  AssertionError("must abort before any fetch")),
              _symbology=_FakeSym(),
              _fetch_history=lambda t: (_ for _ in ()).throw(
                  AssertionError("must abort before the SPY fetch")),
              _fetch_delisting=lambda c: [], _prereg={"k_months": 12})
    with pytest.raises(RuntimeError, match="GB free"):
        run_backfill_13d({"scout": {"backfill": {}}}, _free_gb=lambda p: 2.0, **kw)
    with pytest.raises(RuntimeError, match="GB free"):    # config floor is honored
        run_backfill_13d({"scout": {"backfill": {"min_free_disk_gb": 100}}},
                         _free_gb=lambda p: 50.0, **kw)


def test_free_disk_gb_positive_even_for_missing_path(tmp_path):
    assert free_disk_gb(".") > 0
    assert free_disk_gb(str(tmp_path / "not" / "yet" / "created")) > 0


# --- CLI routing ---

def test_cli_choices_accept_8k_legs():
    parser = build_arg_parser()
    for sig in ("8k", "8k-neg"):
        ns = parser.parse_args(["backfill", "--signal", sig, "--start", "2022-01-01",
                                "--end", "2022-01-31"])
        assert ns.subcommand == "backfill" and ns.signal == sig


def test_cli_routes_8k_legs_to_their_runners(monkeypatch, capsys):
    monkeypatch.setenv("SEC_IDENTITY", "t@example.com")
    calls = []

    def _fake(name):
        def run(config, **kw):
            calls.append(name)
            return {"n_selected": 0}
        return run

    monkeypatch.setattr("shortlist.scout.backfill.run_backfill_8k", _fake("8k"))
    monkeypatch.setattr("shortlist.scout.backfill.run_backfill_8k_neg", _fake("8k-neg"))
    rc1 = daily._run_backfill_cli({"scout": {}}, signal="8k", start=date(2022, 1, 1),
                                  end=date(2022, 1, 31), out_path=None, as_json=True)
    rc2 = daily._run_backfill_cli({"scout": {}}, signal="8k-neg", start=date(2022, 1, 1),
                                  end=date(2022, 1, 31), out_path=None, as_json=True)
    assert rc1 == 0 and rc2 == 0 and calls == ["8k", "8k-neg"]
