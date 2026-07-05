"""CLI-mechanics tests for Task 6: the `backfill` subcommand and `validate --backfill`'s
synthetic-cohort path. See tests/test_scout_validate_measure.py for the measure_cohort
classified-terminal override pins, and tests/test_scout_validate_cli.py for the shared
subparser back-compat pin.
"""
import json
from datetime import date

from shortlist.scout import daily
from shortlist.scout.daily import build_arg_parser, run_validate
from shortlist.scout.firehose import CohortEvent


def test_backfill_subcommand_parses_dates_and_defaults():
    parser = build_arg_parser()
    ns = parser.parse_args(["backfill", "--signal", "13d", "--start", "2022-08-01",
                            "--end", "2022-08-31"])
    assert ns.subcommand == "backfill"
    assert ns.signal == "13d"
    assert ns.start == date(2022, 8, 1)
    assert ns.end == date(2022, 8, 31)
    assert ns.out is None
    assert ns.json is False


def test_main_routes_backfill_subcommand_not_the_daily_run(monkeypatch):
    calls = []
    monkeypatch.setattr(daily, "_run_backfill_cli", lambda *a, **k: calls.append("backfill") or 0)
    monkeypatch.setattr(daily, "run", lambda *a, **k: calls.append("run") or 0)
    rc = daily.main(["backfill", "--signal", "13d", "--start", "2022-08-01",
                     "--end", "2022-08-31"])
    assert rc == 0
    assert calls == ["backfill"]


def test_backfill_cli_missing_identity_returns_2_no_traceback(monkeypatch, capsys):
    monkeypatch.delenv("SEC_IDENTITY", raising=False)
    rc = daily._run_backfill_cli({"scout": {}}, signal="13d", start=date(2022, 8, 1),
                                 end=date(2022, 8, 31), out_path=None, as_json=False)
    assert rc == 2
    err = capsys.readouterr().err
    assert "SEC_IDENTITY" in err


def test_backfill_cli_success_prints_json_summary(monkeypatch, capsys):
    monkeypatch.setenv("SEC_IDENTITY", "t@example.com")
    seen = {}

    def _fake_run_backfill_13d(config, *, start, end, identity, out_path=None, **kw):
        seen["args"] = (start, end, identity, out_path)
        return {"n_selected": 3, "n_measurable": 2, "fraction": 0.667,
               "out_path": "x.jsonl", "written": 3, "by_reason": {}, "by_vintage": {},
               "delisting_by_reason": {}}
    monkeypatch.setattr("shortlist.scout.backfill.run_backfill_13d", _fake_run_backfill_13d)

    rc = daily._run_backfill_cli({"scout": {}}, signal="13d", start=date(2022, 8, 1),
                                 end=date(2022, 8, 31), out_path=None, as_json=True)
    assert rc == 0
    assert seen["args"] == (date(2022, 8, 1), date(2022, 8, 31), "t@example.com", None)
    out = json.loads(capsys.readouterr().out)
    assert out["n_selected"] == 3 and out["written"] == 3


def test_backfill_cli_unsupported_signal_returns_2(monkeypatch, capsys):
    monkeypatch.setenv("SEC_IDENTITY", "t@example.com")
    rc = daily._run_backfill_cli({"scout": {}}, signal="other", start=date(2022, 8, 1),
                                 end=date(2022, 8, 31), out_path=None, as_json=False)
    assert rc == 2
    assert "13d" in capsys.readouterr().err


# --- `validate --backfill PATH`: synthetic-cohort path (pin 3) ---

def _fake_prereg(slug, *, repo_root):
    return {"k_months": 12, "weighting": "equal", "delisting_return": -0.55,
           "min_measurable_frac": 0.90, "min_independent_blocks": 2}


def test_run_validate_events_override_never_instantiates_scout_state(monkeypatch):
    events = [
        {"signal": "edgar:activist_13d", "ticker": "AAA", "event_date": "2024-01-15",
         "strength": 0.9, "gated": False, "composite": 60.0, "meta": {}, "origin": "backfill"},
    ]

    def _boom(*a, **k):
        raise AssertionError("ScoutState must not be instantiated on the --backfill path")
    monkeypatch.setattr(daily, "ScoutState", _boom)

    async def _fake_fetch(tickers, cache_dir, today_iso):
        return {}, {}
    monkeypatch.setattr(daily, "_fetch_validate_data", _fake_fetch)
    monkeypatch.setattr("shortlist.scout.preregister.load_prereg", _fake_prereg)
    monkeypatch.setattr("shortlist.scout.preregister.verify_untampered",
                        lambda slug, *, repo_root, run_as_of: (True, "ok"))

    verdicts = run_validate({"scout": {"validate": {}}}, today=date(2026, 7, 2),
                            lookback_days=365, events_override=events)
    # The one event is composite-defined + non-gated, so it also produces a second
    # (scored_gated) verdict alongside the raw one (Task 6, design B2) — both must still
    # carry the SYNTHETIC label.
    assert len(verdicts) == 2
    assert {v.cohort_type for v in verdicts} == {"raw", "scored_gated"}
    assert all(any("SYNTHETIC" in n for n in v.notes) for v in verdicts)


def test_validate_cli_backfill_path_loads_jsonl_never_touches_state(tmp_path, monkeypatch, capsys):
    ev = CohortEvent(signal="edgar:activist_13d", ticker="AAA", cik=None,
                     event_date=date(2024, 1, 15), as_of_price=None, strength=0.9,
                     gated=None, composite=None, origin="backfill", meta={})
    p = tmp_path / "13d.jsonl"
    p.write_text(json.dumps(ev.to_dict()) + "\n")

    def _boom(*a, **k):
        raise AssertionError("ScoutState must not be instantiated on the --backfill path")
    monkeypatch.setattr(daily, "ScoutState", _boom)

    async def _fake_fetch(tickers, cache_dir, today_iso):
        return {}, {}
    monkeypatch.setattr(daily, "_fetch_validate_data", _fake_fetch)
    monkeypatch.setattr("shortlist.scout.preregister.load_prereg", _fake_prereg)
    monkeypatch.setattr("shortlist.scout.preregister.verify_untampered",
                        lambda slug, *, repo_root, run_as_of: (True, "ok"))

    rc = daily._run_validate_cli({"scout": {"validate": {}}}, today=date(2026, 7, 2),
                                 lookback_days=None, as_json=True, backfill_path=str(p))
    assert rc == 0
    out = capsys.readouterr().out
    assert "SYNTHETIC" in out
