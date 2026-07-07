"""Store format tests: gzip writes, legacy reads, same-day-twin dedup."""
import gzip
import json

from shortlist.data.models import Fundamentals, TickerSnapshot
from shortlist.data.store import captured_days, load, save


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


def test_latest_prefers_gz_when_both_days_present(tmp_path):
    tdir = tmp_path / "LMT"
    tdir.mkdir()
    old = _snap(as_of="2026-07-06T00:00:00+00:00").to_dict()
    old["fundamentals"]["pe_ttm"] = 11.0
    (tdir / "2026-07-06.json").write_text(json.dumps(old, default=str))
    save(_snap(as_of="2026-07-07T00:00:00+00:00"), tmp_path)
    assert load("LMT", tmp_path)["fundamentals"]["pe_ttm"] == 20.0   # 07-07 gz is latest
