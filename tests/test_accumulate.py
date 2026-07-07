import json
from datetime import datetime, timezone

import pytest

from shortlist.data import accumulate as acc
from shortlist.data.accumulate import (
    accumulate, build_arg_parser, is_captured, load_watchlist, store_status,
)
from shortlist.data.models import Analyst, Fundamentals, Profile, TickerSnapshot
from shortlist.data.store import captured_days, load, save


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def _snap(ticker, day=None, coverage_field=True):
    """coverage_field=True clears THIN_MARK (>= 0.5): Profile + Fundamentals +
    Analyst fully populated -> ~0.545 coverage, so accumulate() classifies it
    `captured` (this fixture is used by tests about idempotency/isolation/
    max-tickers/run-log, not about the thin/captured coverage split itself)."""
    day = day or _today()
    s = TickerSnapshot(ticker=ticker, as_of=f"{day}T12:00:00+00:00")
    if coverage_field:
        s.profile = Profile(name=ticker, sector="Tech", industry="Software", sic="7372",
                            exchange="NASDAQ", currency="USD", country="US",
                            market_cap=1e10, beta=1.2, description="desc")
        s.fundamentals = Fundamentals(pe_ttm=20.0, pe_median_5y=18.0, peg=1.5, roe=0.2,
                                      roic=0.15, roic_5y_avg=0.14, gross_margin=0.6,
                                      net_margin=0.2, operating_margin=0.25,
                                      debt_to_equity=0.5, interest_coverage=10.0,
                                      current_ratio=1.5, fcf_yield=0.05)
        s.analyst = Analyst(buy=5, hold=2, sell=1, target_median=100.0, target_high=120.0,
                            target_low=80.0, consensus="buy")
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


def _thin_snap(ticker):
    """~31% coverage (Profile + Analyst fully populated, other KEY_OBJECTS absent) —
    representative of an FMP-gated symbol backfilled from Finnhub/EDGAR only."""
    s = TickerSnapshot(ticker=ticker, as_of=f"{_today()}T12:00:00+00:00")
    s.profile = Profile(name=ticker, sector="Tech", industry="Software", sic="7372",
                        exchange="NASDAQ", currency="USD", country="US",
                        market_cap=1e10, beta=1.2, description="desc")
    s.analyst = Analyst(buy=5, hold=2, sell=1, target_median=100.0, target_high=120.0,
                        target_low=80.0, consensus="buy")
    return s


def fake_collect(tickers, sources, config=None):
    return [_thin_snap(tickers[0])]


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


def test_status_reports_breadth_and_earnings_counts(tmp_path):
    from shortlist.data.models import Earnings
    # 2 dates x 3 tickers, one ticker earnings-bearing
    for day in ("2026-07-06", "2026-07-07"):
        for tk in ("AAA", "BBB", "CCC"):
            snap = TickerSnapshot(ticker=tk, as_of=f"{day}T00:00:00+00:00",
                                  fundamentals=Fundamentals(pe_ttm=10.0),
                                  earnings=Earnings(quarters=4, beats=2) if tk == "AAA" else None)
            save(snap, tmp_path)
    rep = store_status(tmp_path, ["AAA", "BBB", "CCC"], min_dates=24)
    assert rep.per_date == {"2026-07-06": 3, "2026-07-07": 3}
    assert rep.per_date_earnings == {"2026-07-06": 1, "2026-07-07": 1}
    assert rep.min_breadth == 30 and rep.breadth_dates == 0 and rep.breadth_met is False
    assert rep.store_bytes > 0


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


def test_explicit_min_coverage_gates_and_ledgers_as_gated(tmp_path):
    # explicit min_coverage=0.5 still gates a ~31%-coverage snapshot -> NOT saved,
    # but now ledgered under `gated` (thin means saved-but-partial since this task).
    run = accumulate(["THIN"], ["mock"], tmp_path, min_coverage=0.5,
                     collect_fn=fake_collect)
    assert run.gated and run.gated[0][0] == "THIN"
    assert run.thin == [] and run.captured == []
    assert not is_captured("THIN", tmp_path, run.day)


def test_default_saves_thin_snapshot_and_ledgers_it(tmp_path):
    """The point of the breadth fix: a 27-47%-coverage (FMP-gated) snapshot is
    SAVED under the default gate and classified thin (saved, < THIN_MARK)."""
    run = accumulate(["THIN"], ["mock"], tmp_path, collect_fn=fake_collect)  # default min_coverage=0.0
    assert run.thin and run.thin[0][0] == "THIN"
    assert run.gated == []
    assert is_captured("THIN", tmp_path, run.day)


def test_aux_only_snapshot_is_saved_not_failed(tmp_path):
    """A Finnhub-earnings-only snapshot has coverage()==0.0 (aux sections are
    excluded from coverage) yet is precisely the SUE payload — MUST be saved."""
    from shortlist.data.models import Earnings

    def collect_aux(tickers, sources, config=None):
        return [TickerSnapshot(ticker=tickers[0],
                               earnings=Earnings(quarters=4, beats=3))]
    run = accumulate(["AUXO"], ["mock"], tmp_path, collect_fn=collect_aux)
    assert run.thin and run.thin[0][0] == "AUXO"
    assert run.failed == []
    assert is_captured("AUXO", tmp_path, run.day)


def test_truly_empty_snapshot_is_failed_not_saved(tmp_path):
    def collect_empty(tickers, sources, config=None):
        return [TickerSnapshot(ticker=tickers[0])]
    run = accumulate(["NADA"], ["mock"], tmp_path, collect_fn=collect_empty)
    assert run.failed and run.failed[0][0] == "NADA"
    assert not is_captured("NADA", tmp_path, run.day)


def test_run_ledger_carries_gated_count_and_per_ticker_coverage(tmp_path):
    run = accumulate(["THIN"], ["mock"], tmp_path, collect_fn=fake_collect)
    rec = json.loads((tmp_path / "_runs.jsonl").read_text().splitlines()[-1])
    assert rec["gated"] == 0
    assert "THIN" in rec["coverage"]


def test_cli_run_default_min_coverage_is_zero():
    args = build_arg_parser().parse_args(["run"])
    assert args.min_coverage == 0.0


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
