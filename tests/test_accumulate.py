import json
from datetime import datetime, timezone

import pytest

from shortlist.data import accumulate as acc
from shortlist.data.accumulate import (
    accumulate, build_arg_parser, load_watchlist, store_status,
)
from shortlist.data.models import Profile, TickerSnapshot
from shortlist.data.store import captured_days, load, save


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def _snap(ticker, day=None, coverage_field=True):
    day = day or _today()
    s = TickerSnapshot(ticker=ticker, as_of=f"{day}T12:00:00+00:00")
    if coverage_field:
        s.profile = Profile(name=ticker, market_cap=1e10)
    return s


def _fake_collect(snap_by_ticker, calls=None):
    def _c(tickers, sources):
        if calls is not None:
            calls.append(list(tickers))
        out = []
        for t in tickers:
            v = snap_by_ticker.get(t.upper())
            if isinstance(v, Exception):
                raise v
            if v is not None:
                out.append(v)
        return out
    return _c


# --- store: atomic save + captured_days -----------------------------------

def test_captured_days_lists_sorted(tmp_path):
    save(_snap("AAA", "2026-01-02"), tmp_path)
    save(_snap("AAA", "2026-01-01"), tmp_path)
    assert captured_days("AAA", tmp_path) == ["2026-01-01", "2026-01-02"]
    assert captured_days("ZZZ", tmp_path) == []


def test_save_is_atomic_no_tmp_left(tmp_path):
    path = save(_snap("AAA", "2026-01-01"), tmp_path)
    assert path.exists()
    leftovers = list((tmp_path / "AAA").glob(".*tmp"))
    assert leftovers == []                       # temp file replaced, not orphaned


def test_save_crash_before_replace_keeps_prior_file(tmp_path, monkeypatch):
    save(_snap("AAA", "2026-01-01"), tmp_path)           # good v1
    target = tmp_path / "AAA" / "2026-01-01.json.gz"
    before = target.read_bytes()

    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(acc, "save", acc.save)           # no-op; ensure import stable
    monkeypatch.setattr("shortlist.data.store.os.replace", boom)
    with pytest.raises(OSError):
        save(_snap("AAA", "2026-01-01"), tmp_path)        # crash mid-write
    assert target.read_bytes() == before                  # prior file intact, not truncated


# --- accumulate: idempotency, isolation, integrity ------------------------

def test_accumulate_captures_then_skips_without_recollecting(tmp_path):
    calls = []
    snaps = {"AAA": _snap("AAA"), "BBB": _snap("BBB")}
    cf = _fake_collect(snaps, calls)

    r1 = accumulate(["AAA", "BBB"], ["mock"], tmp_path, collect_fn=cf)
    assert {t for t, _ in r1.captured} == {"AAA", "BBB"}
    assert r1.skipped == []
    assert len(calls) == 2                                 # one collect per ticker

    calls.clear()
    r2 = accumulate(["AAA", "BBB"], ["mock"], tmp_path, collect_fn=cf)
    assert r2.captured == []
    assert set(r2.skipped) == {"AAA", "BBB"}
    assert calls == []                                     # idempotent: NO new API calls


def test_accumulate_isolates_failure_and_redacts(tmp_path):
    err = RuntimeError("fmp.quote: https://api?apikey=SUPERSECRET failed 500")
    cf = _fake_collect({"AAA": _snap("AAA"), "BAD": err})
    r = accumulate(["AAA", "BAD"], ["mock"], tmp_path, collect_fn=cf)
    assert [t for t, _ in r.captured] == ["AAA"]           # good one still saved
    assert [t for t, _ in r.failed] == ["BAD"]
    msg = r.failed[0][1]
    assert "SUPERSECRET" not in msg and "redacted" in msg  # key scrubbed


def test_accumulate_rejects_backfilled_past_day(tmp_path):
    cf = _fake_collect({"AAA": _snap("AAA", day="2000-01-01")})   # stale as_of
    r = accumulate(["AAA"], ["mock"], tmp_path, collect_fn=cf)
    assert r.captured == []
    assert [t for t, _ in r.failed] == ["AAA"]
    assert captured_days("AAA", tmp_path) == []            # nothing written


def test_accumulate_respects_max_tickers(tmp_path):
    snaps = {t: _snap(t) for t in ("AAA", "BBB", "CCC")}
    r = accumulate(["AAA", "BBB", "CCC"], ["mock"], tmp_path, max_tickers=2,
                   collect_fn=_fake_collect(snaps))
    assert r.attempted == 2
    assert len(r.captured) == 2


def test_accumulate_writes_run_log(tmp_path):
    cf = _fake_collect({"AAA": _snap("AAA")})
    accumulate(["AAA"], ["mock"], tmp_path, collect_fn=cf)
    lines = (tmp_path / "_runs.jsonl").read_text().splitlines()
    rec = json.loads(lines[-1])
    assert rec["captured"] == 1 and rec["day"] == _today()


# --- status ----------------------------------------------------------------

def test_store_status_threshold(tmp_path):
    for i in range(1, 25):                                  # 24 distinct dates
        save(_snap("AAA", f"2026-01-{i:02d}"), tmp_path)
    rep = store_status(tmp_path, ["AAA"], min_dates=24)
    assert rep.n_dates == 24 and rep.threshold_met is True
    assert rep.per_ticker == {"AAA": 24}

    rep2 = store_status(tmp_path, ["AAA"], min_dates=25)
    assert rep2.threshold_met is False


def test_status_empty_store(tmp_path):
    rep = store_status(tmp_path, ["AAA", "BBB"])
    assert rep.n_dates == 0 and rep.threshold_met is False


# --- watchlist + CLI -------------------------------------------------------

def test_load_watchlist_default_and_csv():
    d = load_watchlist("default")
    assert len(d) >= 10 and "AAPL" in d and all(t == t.upper() for t in d)
    assert load_watchlist("gev, lmt") == ["GEV", "LMT"]


def test_max_tickers_zero_captures_nothing(tmp_path):
    snaps = {t: _snap(t) for t in ("AAA", "BBB")}
    r = accumulate(["AAA", "BBB"], ["mock"], tmp_path, max_tickers=0,
                   collect_fn=_fake_collect(snaps))
    assert r.attempted == 0 and r.captured == []


def test_min_coverage_gate_skips_thin_and_does_not_save(tmp_path):
    # a bare (no-fields) snapshot has ~0 coverage -> thin, must NOT pollute the store
    cf = _fake_collect({"AAA": _snap("AAA", coverage_field=False)})
    r = accumulate(["AAA"], ["mock"], tmp_path, min_coverage=0.5, collect_fn=cf)
    assert r.captured == []
    assert [t for t, _ in r.thin] == ["AAA"]
    assert captured_days("AAA", tmp_path) == []        # not saved -> not counted toward 24


def test_main_run_exit_0_and_status(tmp_path, monkeypatch):
    monkeypatch.setattr(acc, "collect", _fake_collect({"AAA": _snap("AAA")}))
    rc = acc.main(["run", "--tickers", "AAA", "--sources", "mock",
                   "--root", str(tmp_path), "--min-coverage", "0"])
    assert rc == 0
    assert load("AAA", tmp_path)["ticker"] == "AAA"    # really saved via main()
    rc2 = acc.main(["status", "--tickers", "AAA", "--root", str(tmp_path)])
    assert rc2 == 0


def test_main_run_all_failed_exits_1(tmp_path, monkeypatch):
    monkeypatch.setattr(acc, "collect", _fake_collect({"BAD": RuntimeError("boom")}))
    rc = acc.main(["run", "--tickers", "BAD", "--sources", "mock",
                   "--root", str(tmp_path), "--min-coverage", "0"])
    assert rc == 1                                     # failure surfaced to the operator/timer


def test_cli_parser_run_and_status_defaults():
    ap = build_arg_parser()
    r = ap.parse_args(["run"])
    assert r.cmd == "run" and r.sources == "fmp,finnhub" and r.max_tickers == 15
    s = ap.parse_args(["status"])
    assert s.cmd == "status" and s.min_dates == 24
