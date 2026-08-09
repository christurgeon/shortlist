"""Store format tests: gzip writes, legacy reads, same-day-twin dedup."""
import gzip
import json

from shortlist.data.models import Fundamentals, TickerSnapshot
from shortlist.data.store import capture_days, captured_days, load, save


def _snap(ticker="LMT", as_of="2026-07-07T00:00:00+00:00"):
    return TickerSnapshot(ticker=ticker, as_of=as_of,
                          fundamentals=Fundamentals(pe_ttm=20.0))


def test_save_writes_gzip_and_roundtrips(tmp_path):
    path = save(_snap(), tmp_path)
    assert path.name == "2026-07-07.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as f:
        raw = json.load(f)
    assert raw["ticker"] == "LMT"
    assert load("LMT", tmp_path)["fundamentals"]["pe_ttm"] == 20.0
    assert load("LMT", tmp_path, day="2026-07-07")["ticker"] == "LMT"


def test_load_reads_legacy_plain_json(tmp_path):
    tdir = tmp_path / "LMT"
    tdir.mkdir()
    (tdir / "2026-07-06.json").write_text(json.dumps(_snap(as_of="2026-07-06T00:00:00+00:00").to_dict(), default=str))
    assert load("LMT", tmp_path)["ticker"] == "LMT"
    assert load("LMT", tmp_path, day="2026-07-06")["ticker"] == "LMT"
    assert captured_days("LMT", tmp_path) == ["2026-07-06"]


def test_captured_days_normalizes_suffixes_and_dedupes_twins(tmp_path):
    tdir = tmp_path / "LMT"
    tdir.mkdir()
    legacy = _snap(as_of="2026-07-06T00:00:00+00:00").to_dict()
    (tdir / "2026-07-06.json").write_text(json.dumps(legacy, default=str))
    save(_snap(as_of="2026-07-07T00:00:00+00:00"), tmp_path)
    # No ".json"-suffixed day keys (the Path.stem double-suffix trap).
    assert captured_days("LMT", tmp_path) == ["2026-07-06", "2026-07-07"]


def test_save_unlinks_legacy_twin_for_same_day(tmp_path):
    tdir = tmp_path / "LMT"
    tdir.mkdir()
    (tdir / "2026-07-07.json").write_text(json.dumps(_snap().to_dict(), default=str))
    save(_snap(), tmp_path)   # --force re-run post-deploy case
    assert not (tdir / "2026-07-07.json").exists()
    assert (tdir / "2026-07-07.json.gz").exists()
    assert captured_days("LMT", tmp_path) == ["2026-07-07"]   # counted once


def test_capture_days_unions_distinct_days_across_tickers(tmp_path):
    """Store-wide history depth: the union of days, deduped across tickers (a
    day one ticker missed is still a captured day), never double-counting a
    same-day .json/.json.gz twin."""
    save(_snap("AAA", "2026-07-06T00:00:00+00:00"), tmp_path)
    save(_snap("AAA", "2026-07-07T00:00:00+00:00"), tmp_path)
    save(_snap("BBB", "2026-07-07T00:00:00+00:00"), tmp_path)   # same day, 2nd ticker
    save(_snap("BBB", "2026-07-08T00:00:00+00:00"), tmp_path)
    assert capture_days(tmp_path) == ["2026-07-06", "2026-07-07", "2026-07-08"]


def test_capture_days_empty_for_missing_root(tmp_path):
    assert capture_days(tmp_path / "nope") == []


def test_capture_days_ignores_non_ticker_files_at_store_root(tmp_path):
    """The live store keeps `_runs.jsonl` beside the ticker directories, so a stray
    root-level file must not disturb the day list. API-level guarantee only: it holds
    both via the `is_dir()` skip AND because globbing a non-directory yields nothing,
    so this does NOT pin either mechanism on its own (verified by removing the skip)."""
    save(_snap("AAA", "2026-07-06T00:00:00+00:00"), tmp_path)
    (tmp_path / "_runs.jsonl").write_text('{"run": 1}\n')
    assert capture_days(tmp_path) == ["2026-07-06"]


def test_save_failure_cleans_up_temp_file(tmp_path, monkeypatch):
    # A failed gzip write must propagate the error AND not leak the .tmp file.
    import pytest

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("shortlist.data.store.gzip.open", _boom)
    with pytest.raises(OSError, match="disk full"):
        save(_snap(), tmp_path)
    tdir = tmp_path / "LMT"
    leftovers = [p.name for p in tdir.iterdir()] if tdir.is_dir() else []
    assert not any(n.endswith(".tmp") for n in leftovers), leftovers
    assert not (tdir / "2026-07-07.json.gz").exists()   # nothing half-written


def test_latest_prefers_gz_when_both_days_present(tmp_path):
    tdir = tmp_path / "LMT"
    tdir.mkdir()
    old = _snap(as_of="2026-07-06T00:00:00+00:00").to_dict()
    old["fundamentals"]["pe_ttm"] = 11.0
    (tdir / "2026-07-06.json").write_text(json.dumps(old, default=str))
    save(_snap(as_of="2026-07-07T00:00:00+00:00"), tmp_path)
    assert load("LMT", tmp_path)["fundamentals"]["pe_ttm"] == 20.0   # 07-07 gz is latest
