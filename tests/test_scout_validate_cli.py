import json
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

import yaml

from shortlist.backtest.prices import PriceHistory
from shortlist.scout import daily
from shortlist.scout.daily import (VALIDATE_LATEST_PATH, _load_validation_digest,
                                   _run_validate_cli, build_arg_parser, run_validate)
from shortlist.scout.validate import SignalVerdict


def test_validate_subcommand_parses_without_breaking_bare_run():
    parser = build_arg_parser()
    # bare (no subcommand) still parses -> daily run
    ns = parser.parse_args([])
    assert getattr(ns, "subcommand", None) in (None, "run")
    ns2 = parser.parse_args(["validate", "--lookback-days", "365", "--json"])
    assert ns2.subcommand == "validate"
    assert ns2.lookback_days == 365 and ns2.json is True
    # backfill subcommand also parses, and its presence must not disturb the bare-run route
    # (subparser back-compat pin — see tests/test_scout_backfill_cli.py for the CLI mechanics).
    ns3 = parser.parse_args(["backfill", "--signal", "13d", "--start", "2022-08-01",
                             "--end", "2022-08-31"])
    assert ns3.subcommand == "backfill"
    ns4 = parser.parse_args([])
    assert getattr(ns4, "subcommand", None) in (None, "run")


class _FakeState:
    def __init__(self, events):
        self._events = events

    def firehose_events(self, on, lookback_days):
        return self._events


def test_malformed_prereg_isolates_to_one_signal(monkeypatch):
    """A malformed committed pre-reg YAML for ONE signal must degrade only that signal to
    INSUFFICIENT (a clear note, not a raise) while the loop continues to evaluate the
    others — the anti-abort guarantee. Regression for the too-narrow (OSError, ValueError)
    catch that let yaml.YAMLError escape and kill the whole eval loop."""
    events = [
        {"signal": "bad:signal", "ticker": "AAA", "event_date": "2024-01-15",
         "strength": 0.5, "gated": False, "composite": 60.0},
        {"signal": "good:signal", "ticker": "BBB", "event_date": "2024-06-15",
         "strength": 0.5, "gated": False, "composite": 60.0},
    ]
    monkeypatch.setattr(daily, "ScoutState", lambda *a, **k: _FakeState(events))

    # No network: FF3 + histories come back empty (histories empty => events non-measurable,
    # so the "good" signal lands INSUFFICIENT on measurable-fraction — still a verdict, no raise).
    async def _fake_fetch(tickers, cache_dir, today_iso):
        return {}, {}
    monkeypatch.setattr(daily, "_fetch_validate_data", _fake_fetch)

    def _fake_load_prereg(slug, *, repo_root):
        if slug == "bad_signal":
            raise yaml.YAMLError("mapping values are not allowed here")
        return {"k_months": 12, "weighting": "equal", "delisting_return": -0.55,
                "min_measurable_frac": 0.90, "min_independent_blocks": 2}
    # load_prereg / verify_untampered are imported INSIDE run_validate, so patch the
    # source module (the local binding resolves from it at call time).
    monkeypatch.setattr("shortlist.scout.preregister.load_prereg", _fake_load_prereg)
    monkeypatch.setattr("shortlist.scout.preregister.verify_untampered",
                        lambda slug, *, repo_root, run_as_of: (True, "ok"))

    verdicts = run_validate({"scout": {"validate": {}}}, today=date(2026, 7, 2),
                            lookback_days=900)
    by_sig = {v.signal: v for v in verdicts}
    # Both signals evaluated — the malformed one did NOT abort the loop.
    assert set(by_sig) == {"bad:signal", "good:signal"}
    assert by_sig["bad:signal"].verdict == "INSUFFICIENT"
    assert "unparsable" in " ".join(by_sig["bad:signal"].notes).lower()
    # The good signal produced its own verdict (INSUFFICIENT here — no measurable data).
    assert by_sig["good:signal"].verdict == "INSUFFICIENT"


def test_backfill_sentinel_tickers_skip_yahoo_fetch_but_stay_in_cohort(monkeypatch):
    """Backfill sentinel tickers ("CIK:0001823575", "CIK:unknown-<acc>") have no resolvable
    price history and would each fire a doomed Yahoo fetch_history request (VPS WAF
    protection). run_validate must exclude them from the fetch list while keeping their
    events in the cohort (they count non-measurable, never silently dropped)."""
    events = [
        {"signal": "edgar:activist_13d", "ticker": "AAPL", "event_date": "2024-01-15",
         "strength": 0.5, "gated": False, "composite": 60.0},
        {"signal": "edgar:activist_13d", "ticker": "CIK:0001823575", "event_date": "2024-02-15",
         "strength": 0.5, "gated": False, "composite": 60.0},
    ]

    fetched_tickers: list[str] = []

    async def _fake_fetch(tickers, cache_dir, today_iso):
        fetched_tickers.extend(tickers)
        return {}, {}
    monkeypatch.setattr(daily, "_fetch_validate_data", _fake_fetch)

    def _fake_load_prereg(slug, *, repo_root):
        return {"k_months": 12, "weighting": "equal", "delisting_return": -0.55,
                "min_measurable_frac": 0.90, "min_independent_blocks": 2}
    monkeypatch.setattr("shortlist.scout.preregister.load_prereg", _fake_load_prereg)
    monkeypatch.setattr("shortlist.scout.preregister.verify_untampered",
                        lambda slug, *, repo_root, run_as_of: (True, "ok"))

    verdicts = run_validate({"scout": {"validate": {}}}, today=date(2026, 7, 2),
                            lookback_days=900, events_override=events)

    # Only the real ticker reaches the Yahoo-fetch seam — the CIK: sentinel is excluded.
    assert fetched_tickers == ["AAPL"]
    # Both events still make up the cohort (n_selected == 2); the sentinel is
    # non-measurable, not dropped. Both events are composite-defined + non-gated, so a
    # second (scored_gated) verdict is now also emitted (Task 6, design B2) alongside the
    # raw one.
    assert len(verdicts) == 2
    assert verdicts[0].cohort_type == "raw"
    assert verdicts[0].n_selected == 2
    assert verdicts[1].cohort_type == "scored_gated"
    assert verdicts[1].n_selected == 2


# --- Task 6: scored_gated cohort verdict + double-sort surfacing (design B2 + v2 §6/§8) ---

def _hist(ticker: str) -> PriceHistory:
    """A synthetic daily price series long enough (2023-12-01..~mid-2024) that
    forward_return(t, horizon_months=1) resolves for any event dated Jan-Mar 2024."""
    start = date(2023, 12, 1)
    days = 200
    dates = [start + timedelta(days=i) for i in range(days)]
    closes = [100.0 * (1.001 ** i) for i in range(days)]
    return PriceHistory(ticker=ticker, dates=dates, closes=closes)


def _fake_prereg_factory(**overrides):
    base = {"k_months": 1, "weighting": "equal", "delisting_return": None,
           "min_measurable_frac": 0.0, "min_bucket_events": 1,
           "min_independent_blocks": 1, "factor_model": "ff3"}
    base.update(overrides)

    def _fake_load_prereg(slug, *, repo_root):
        return base
    return _fake_load_prereg


def test_mixed_cohort_produces_scored_gated_verdict_with_double_sort(monkeypatch):
    """A cohort with composite-defined + non-gated events (scored), a no-composite event
    (raw-only), and a composite-defined but GATED event (ds-only, per the gate-agnostic
    double-sort set) must produce exactly two verdicts: the raw one (all 4 events) and a
    second scored_gated verdict (only the 2 non-gated composite events) carrying the
    reconstruction caveat note + a real double_sort dict."""
    events = [
        {"signal": "test:mixed", "ticker": "AAA", "event_date": "2024-01-15",
         "strength": 0.9, "gated": False, "composite": 80.0},
        {"signal": "test:mixed", "ticker": "BBB", "event_date": "2024-01-15",
         "strength": 0.9, "gated": False, "composite": 20.0},
        {"signal": "test:mixed", "ticker": "CCC", "event_date": "2024-02-15",
         "strength": 0.9, "gated": None, "composite": None},
        {"signal": "test:mixed", "ticker": "DDD", "event_date": "2024-03-15",
         "strength": 0.9, "gated": True, "composite": 90.0},
    ]
    hists = {"AAA": _hist("AAA"), "BBB": _hist("BBB"), "DDD": _hist("DDD")}

    async def _fake_fetch(tickers, cache_dir, today_iso):
        return {}, hists
    monkeypatch.setattr(daily, "_fetch_validate_data", _fake_fetch)
    monkeypatch.setattr("shortlist.scout.preregister.load_prereg", _fake_prereg_factory())
    monkeypatch.setattr("shortlist.scout.preregister.verify_untampered",
                        lambda slug, *, repo_root, run_as_of: (True, "ok"))

    verdicts = run_validate({"scout": {"validate": {}}}, today=date(2026, 7, 2),
                            lookback_days=900, events_override=events)

    assert len(verdicts) == 2
    raw, scored = verdicts
    assert raw.cohort_type == "raw"
    assert raw.n_selected == 4          # AAA, BBB, CCC, DDD all in the raw cohort
    assert scored.cohort_type == "scored_gated"
    assert scored.n_selected == 2       # only AAA, BBB (composite-defined + non-gated)
    assert any("composites not comparable to live" in n for n in scored.notes)
    # Double-sort runs over the GATE-AGNOSTIC composite-defined set (AAA, BBB, DDD) --
    # a strict superset of the scored cohort (AAA, BBB) since DDD is gated but composite-defined.
    assert scored.double_sort is not None
    assert scored.double_sort["n_high"] + scored.double_sort["n_low"] == 3
    assert scored.double_sort["n_low"] >= 1 and scored.double_sort["n_high"] >= 1


def test_no_composite_cohort_produces_only_raw_verdict(monkeypatch):
    """Old-shaped events (no `composite` field at all -- pre-reconstruction JSONLs, or the
    live firehose) must never manufacture a phantom scored_gated verdict (back-compat)."""
    events = [
        {"signal": "test:nocomposite", "ticker": "AAA", "event_date": "2024-01-15",
         "strength": 0.9},
    ]

    async def _fake_fetch(tickers, cache_dir, today_iso):
        return {}, {}
    monkeypatch.setattr(daily, "_fetch_validate_data", _fake_fetch)
    monkeypatch.setattr("shortlist.scout.preregister.load_prereg", _fake_prereg_factory())
    monkeypatch.setattr("shortlist.scout.preregister.verify_untampered",
                        lambda slug, *, repo_root, run_as_of: (True, "ok"))

    verdicts = run_validate({"scout": {"validate": {}}}, today=date(2026, 7, 2),
                            lookback_days=900, events_override=events)
    assert len(verdicts) == 1
    assert verdicts[0].cohort_type == "raw"


def test_interim_label_appears_when_run_before_verdict_as_of(monkeypatch):
    """I1: a run date strictly before the prereg's `verdict_as_of` gets a permanent INTERIM
    label on every verdict for that signal (the canonical verdict is the on/after-date one)."""
    events = [
        {"signal": "test:interim", "ticker": "AAA", "event_date": "2024-01-15",
         "strength": 0.9},
    ]

    async def _fake_fetch(tickers, cache_dir, today_iso):
        return {}, {}
    monkeypatch.setattr(daily, "_fetch_validate_data", _fake_fetch)
    monkeypatch.setattr("shortlist.scout.preregister.load_prereg",
                        _fake_prereg_factory(verdict_as_of="2026-12-31"))
    monkeypatch.setattr("shortlist.scout.preregister.verify_untampered",
                        lambda slug, *, repo_root, run_as_of: (True, "ok"))

    verdicts = run_validate({"scout": {"validate": {}}}, today=date(2026, 7, 2),
                            lookback_days=900, events_override=events)
    assert len(verdicts) == 1
    joined = " ".join(verdicts[0].notes)
    assert "INTERIM — before registered verdict_as_of 2026-12-31" in joined


def test_interim_label_absent_when_verdict_as_of_key_missing(monkeypatch):
    """Back-compat: a prereg file without `verdict_as_of` (predates I1) must never label
    a verdict INTERIM."""
    events = [
        {"signal": "test:nointerim", "ticker": "AAA", "event_date": "2024-01-15",
         "strength": 0.9},
    ]

    async def _fake_fetch(tickers, cache_dir, today_iso):
        return {}, {}
    monkeypatch.setattr(daily, "_fetch_validate_data", _fake_fetch)
    monkeypatch.setattr("shortlist.scout.preregister.load_prereg", _fake_prereg_factory())
    monkeypatch.setattr("shortlist.scout.preregister.verify_untampered",
                        lambda slug, *, repo_root, run_as_of: (True, "ok"))

    verdicts = run_validate({"scout": {"validate": {}}}, today=date(2026, 7, 2),
                            lookback_days=900, events_override=events)
    assert len(verdicts) == 1
    assert not any("INTERIM" in n for n in verdicts[0].notes)


def test_interim_label_absent_when_run_date_on_or_after_verdict_as_of(monkeypatch):
    """A run ON or AFTER the registered verdict_as_of is the canonical verdict — no INTERIM
    label."""
    events = [
        {"signal": "test:canonical", "ticker": "AAA", "event_date": "2024-01-15",
         "strength": 0.9},
    ]

    async def _fake_fetch(tickers, cache_dir, today_iso):
        return {}, {}
    monkeypatch.setattr(daily, "_fetch_validate_data", _fake_fetch)
    monkeypatch.setattr("shortlist.scout.preregister.load_prereg",
                        _fake_prereg_factory(verdict_as_of="2026-12-31"))
    monkeypatch.setattr("shortlist.scout.preregister.verify_untampered",
                        lambda slug, *, repo_root, run_as_of: (True, "ok"))

    verdicts = run_validate({"scout": {"validate": {}}}, today=date(2026, 12, 31),
                            lookback_days=900, events_override=events)
    assert len(verdicts) == 1
    assert not any("INTERIM" in n for n in verdicts[0].notes)


def test_malformed_verdict_as_of_no_interim_label_no_crash(monkeypatch):
    """`_parse_prereg_date` defensively returns None on an unparsable `verdict_as_of` --
    a malformed value in a committed prereg must degrade to "no INTERIM label" (same as a
    missing key), never raise through run_validate."""
    events = [
        {"signal": "test:malformed_date", "ticker": "AAA", "event_date": "2024-01-15",
         "strength": 0.9},
    ]

    async def _fake_fetch(tickers, cache_dir, today_iso):
        return {}, {}
    monkeypatch.setattr(daily, "_fetch_validate_data", _fake_fetch)
    monkeypatch.setattr("shortlist.scout.preregister.load_prereg",
                        _fake_prereg_factory(verdict_as_of="not-a-date"))
    monkeypatch.setattr("shortlist.scout.preregister.verify_untampered",
                        lambda slug, *, repo_root, run_as_of: (True, "ok"))

    verdicts = run_validate({"scout": {"validate": {}}}, today=date(2026, 7, 2),
                            lookback_days=900, events_override=events)
    assert len(verdicts) == 1
    assert not any("INTERIM" in n for n in verdicts[0].notes)


def test_tamper_check_failed_and_interim_both_present(monkeypatch):
    """Minor (review): when a signal is BOTH un-pre-registered (tamper-check failed) AND
    the run predates the registered `verdict_as_of`, both notes must appear -- neither
    guard should suppress the other."""
    events = [
        {"signal": "test:tampered_and_interim", "ticker": "AAA", "event_date": "2024-01-15",
         "strength": 0.9},
    ]

    async def _fake_fetch(tickers, cache_dir, today_iso):
        return {}, {}
    monkeypatch.setattr(daily, "_fetch_validate_data", _fake_fetch)
    monkeypatch.setattr("shortlist.scout.preregister.load_prereg",
                        _fake_prereg_factory(verdict_as_of="2026-12-31"))
    monkeypatch.setattr("shortlist.scout.preregister.verify_untampered",
                        lambda slug, *, repo_root, run_as_of: (False, "prereg hash mismatch"))

    verdicts = run_validate({"scout": {"validate": {}}}, today=date(2026, 7, 2),
                            lookback_days=900, events_override=events)
    assert len(verdicts) == 1
    joined = " ".join(verdicts[0].notes)
    assert "NOT PRE-REGISTERED: prereg hash mismatch" in joined
    assert "INTERIM — before registered verdict_as_of 2026-12-31" in joined


def test_scored_cohort_exists_but_double_sort_gate_fails(monkeypatch):
    """A scored cohort exists (composite-defined, non-gated events) but no price history
    means the gate-agnostic double-sort set has no measurable events -> double_sort returns
    None. The scored_gated verdict must still be emitted, carrying an explicit
    'insufficient blocks or bucket size' note (v2 §2's run-log marker)."""
    events = [
        {"signal": "test:thin", "ticker": "AAA", "event_date": "2024-01-15",
         "strength": 0.9, "gated": False, "composite": 80.0},
        {"signal": "test:thin", "ticker": "BBB", "event_date": "2024-01-15",
         "strength": 0.9, "gated": False, "composite": 20.0},
    ]

    async def _fake_fetch(tickers, cache_dir, today_iso):
        return {}, {}          # no price history for either ticker -> non-measurable
    monkeypatch.setattr(daily, "_fetch_validate_data", _fake_fetch)
    monkeypatch.setattr("shortlist.scout.preregister.load_prereg",
                        _fake_prereg_factory(min_bucket_events=5, min_independent_blocks=2))
    monkeypatch.setattr("shortlist.scout.preregister.verify_untampered",
                        lambda slug, *, repo_root, run_as_of: (True, "ok"))

    verdicts = run_validate({"scout": {"validate": {}}}, today=date(2026, 7, 2),
                            lookback_days=900, events_override=events)
    assert len(verdicts) == 2
    scored = verdicts[1]
    assert scored.cohort_type == "scored_gated"
    assert scored.double_sort is None
    assert any("insufficient blocks or bucket size" in n for n in scored.notes)


def test_json_asdict_structure_includes_cohort_type_and_double_sort(monkeypatch):
    """--json serialization (json.dumps([asdict(v) for v in verdicts])) must expose
    cohort_type on every verdict and a real double_sort dict on the scored one, and the
    whole structure must be JSON-serializable."""
    events = [
        {"signal": "test:json", "ticker": "AAA", "event_date": "2024-01-15",
         "strength": 0.9, "gated": False, "composite": 80.0},
        {"signal": "test:json", "ticker": "BBB", "event_date": "2024-01-15",
         "strength": 0.9, "gated": False, "composite": 20.0},
    ]
    hists = {"AAA": _hist("AAA"), "BBB": _hist("BBB")}

    async def _fake_fetch(tickers, cache_dir, today_iso):
        return {}, hists
    monkeypatch.setattr(daily, "_fetch_validate_data", _fake_fetch)
    monkeypatch.setattr("shortlist.scout.preregister.load_prereg", _fake_prereg_factory())
    monkeypatch.setattr("shortlist.scout.preregister.verify_untampered",
                        lambda slug, *, repo_root, run_as_of: (True, "ok"))

    verdicts = run_validate({"scout": {"validate": {}}}, today=date(2026, 7, 2),
                            lookback_days=900, events_override=events)
    dicts = [asdict(v) for v in verdicts]
    assert all("cohort_type" in d for d in dicts)
    assert dicts[0]["cohort_type"] == "raw"
    assert dicts[1]["cohort_type"] == "scored_gated"
    assert dicts[1]["double_sort"] is not None
    assert "n_high" in dicts[1]["double_sort"]
    # B2: every verdict's --json shape carries n_immature/n_events (both 0 here -- no
    # price history means every event is non-measurable-and-counted, never immature).
    assert all("n_immature" in d and "n_events" in d for d in dicts)
    json.dumps(dicts, default=str)          # must not raise -- serializable end to end


def test_print_validate_table_shows_cohort_type_and_double_sort_line(capsys):
    """The text-table renderer must label every verdict row with its cohort_type and print
    a compact double-sort line when a double_sort dict is present."""
    raw = SignalVerdict(signal="s", verdict="HOLD", ir=None, alpha_monthly=None,
                        alpha_ci=None, effective_blocks=1, n_selected=4, n_measurable=4,
                        measurable_fraction=1.0, sensitivity_flip=False, cohort_type="raw")
    scored = SignalVerdict(
        signal="s", verdict="HOLD", ir=None, alpha_monthly=None, alpha_ci=None,
        effective_blocks=1, n_selected=2, n_measurable=2, measurable_fraction=1.0,
        sensitivity_flip=False, cohort_type="scored_gated",
        double_sort={"n_high": 2, "n_low": 2, "months": 3, "effective_blocks": 3,
                     "spread_alpha_monthly": 0.01, "spread_ci": (0.001, 0.02),
                     "high_ir": 1.2, "low_ir": 0.3})
    daily._print_validate_table([raw, scored])
    out = capsys.readouterr().out
    assert "COHORT" in out
    assert "raw" in out and "scored_gated" in out
    assert "double-sort:" in out
    assert "blocks=3" in out
    assert "n=2/2" in out


def test_print_validate_table_shows_immature_count_when_present(capsys):
    """B2/I4: a young live cohort must read '0/0 (+350 immature)', never a bare '0/0'."""
    v = SignalVerdict(signal="s", verdict="INSUFFICIENT", ir=None, alpha_monthly=None,
                      alpha_ci=None, effective_blocks=0, n_selected=0, n_measurable=0,
                      measurable_fraction=0.0, sensitivity_flip=False, cohort_type="raw",
                      n_immature=350, n_events=350)
    daily._print_validate_table([v])
    out = capsys.readouterr().out
    assert "(+350 immature)" in out
    assert "mature-only (H2" in out


def test_print_validate_table_omits_immature_suffix_when_zero(capsys):
    v = SignalVerdict(signal="s", verdict="HOLD", ir=None, alpha_monthly=None,
                      alpha_ci=None, effective_blocks=1, n_selected=4, n_measurable=4,
                      measurable_fraction=1.0, sensitivity_flip=False, cohort_type="raw")
    daily._print_validate_table([v])
    out = capsys.readouterr().out
    # Per-row "(+N immature)" suffix is display-only and must be omitted when zero -- but
    # the mature-only-denominator footer (below) is now unconditional (review follow-up)
    # and its fixed boilerplate text legitimately contains the word "immature", so assert
    # on the row-level suffix shape specifically, not a blanket substring-absence.
    assert "(+0 immature)" not in out
    assert "(+4 immature)" not in out


def test_print_validate_table_marks_a_suppressed_level_not_a_bare_dash(capsys):
    """A level suppressed by the measurability floor must read differently from one that
    could not be computed -- a bare '-' invites re-deriving it by hand. BOTH level columns
    (IR and ALPHA/mo) must say so: asserting a single 'SUPP' anywhere passes against a
    half-implemented renderer that only marks one of them."""
    v = SignalVerdict(signal="s", verdict="INSUFFICIENT", ir=None, alpha_monthly=None,
                      alpha_ci=None, effective_blocks=4, n_selected=100, n_measurable=62,
                      measurable_fraction=0.62, sensitivity_flip=False, cohort_type="raw",
                      alpha_suppressed=True)
    daily._print_validate_table([v])
    out = capsys.readouterr().out
    assert out.count("SUPP") == 2


def test_print_validate_table_prints_real_levels_when_not_suppressed(capsys):
    """The negative half: a verdict carrying real numbers must still print them, so the
    marker above can't be a renderer that says SUPP unconditionally."""
    v = SignalVerdict(signal="s", verdict="KILL", ir=-1.25, alpha_monthly=-0.0432,
                      alpha_ci=(-0.06, -0.02), effective_blocks=4, n_selected=100,
                      n_measurable=98, measurable_fraction=0.98, sensitivity_flip=False,
                      cohort_type="raw")
    daily._print_validate_table([v])
    out = capsys.readouterr().out
    assert "SUPP" not in out
    assert "-1.25" in out and "-0.0432" in out


def test_print_validate_table_double_sort_none_shows_note_not_ds_line(capsys):
    """When double_sort is None (gate failed), no compact double-sort line is printed --
    the explanatory note (already in .notes) is the only surfacing."""
    scored = SignalVerdict(
        signal="s", verdict="INSUFFICIENT", ir=None, alpha_monthly=None, alpha_ci=None,
        effective_blocks=0, n_selected=2, n_measurable=0, measurable_fraction=0.0,
        sensitivity_flip=False, cohort_type="scored_gated", double_sort=None,
        notes=["double-sort: insufficient blocks or bucket size"])
    daily._print_validate_table([scored])
    out = capsys.readouterr().out
    assert "insufficient blocks or bucket size" in out
    assert "double-sort: spread" not in out


# --- Task 1 (digest-verdicts plan): persist boundary -- scout/validate-latest.json ---

def _sample_verdicts() -> list[SignalVerdict]:
    raw = SignalVerdict(signal="test:sig", verdict="HOLD", ir=0.4, alpha_monthly=0.01,
                        alpha_ci=(0.001, 0.02), effective_blocks=3, n_selected=10,
                        n_measurable=8, measurable_fraction=0.8, sensitivity_flip=False,
                        cohort_type="raw", n_immature=2, n_events=12)
    scored = SignalVerdict(
        signal="test:sig", verdict="KILL", ir=-0.2, alpha_monthly=-0.005, alpha_ci=None,
        effective_blocks=3, n_selected=6, n_measurable=6, measurable_fraction=1.0,
        sensitivity_flip=True, cohort_type="scored_gated",
        double_sort={"n_high": 3, "n_low": 3, "months": 3, "effective_blocks": 3,
                     "spread_alpha_monthly": 0.02, "spread_ci": (0.001, 0.03),
                     "high_ir": 1.1, "low_ir": -0.3})
    return [raw, scored]


def test_run_validate_cli_live_path_persists_latest_json(tmp_path, monkeypatch, capsys):
    """The live (no --backfill) validate path writes scout/validate-latest.json with
    source "live" after computing verdicts."""
    monkeypatch.chdir(tmp_path)
    verdicts = _sample_verdicts()
    monkeypatch.setattr(daily, "run_validate", lambda *a, **k: verdicts)

    today = date(2026, 7, 5)
    rc = _run_validate_cli({"scout": {"validate": {}}}, today=today, lookback_days=365,
                           as_json=False)
    assert rc == 0

    out_path = tmp_path / VALIDATE_LATEST_PATH
    assert out_path.exists()
    payload = json.loads(out_path.read_text())
    assert payload["as_of"] == "2026-07-05"
    assert payload["source"] == "live"
    assert len(payload["verdicts"]) == 2
    # Verdicts still printed to the table on the normal (never-blocked) path.
    out = capsys.readouterr().out
    assert "test:sig" in out


def test_run_validate_cli_backfill_path_persists_latest_json_with_basename_label(
        tmp_path, monkeypatch):
    """The `validate --backfill PATH` path labels the source as "backfill:<basename>" --
    the basename only, not the full (possibly absolute) path."""
    monkeypatch.chdir(tmp_path)
    verdicts = _sample_verdicts()
    monkeypatch.setattr(daily, "run_validate", lambda *a, **k: verdicts)

    backfill_dir = tmp_path / "some" / "nested" / "dir"
    backfill_dir.mkdir(parents=True)
    backfill_file = backfill_dir / "13d-2024-01-01-2024-06-30.jsonl"
    backfill_file.write_text("")
    monkeypatch.setattr("shortlist.scout.backfill.load_backfill_events", lambda path: [])

    today = date(2026, 7, 5)
    rc = _run_validate_cli({"scout": {"validate": {}}}, today=today, lookback_days=365,
                           as_json=True, backfill_path=str(backfill_file))
    assert rc == 0

    payload = json.loads((tmp_path / VALIDATE_LATEST_PATH).read_text())
    assert payload["source"] == "backfill:13d-2024-01-01-2024-06-30.jsonl"


def test_run_validate_cli_persist_write_failure_warns_but_exit_code_and_output_unchanged(
        tmp_path, monkeypatch, capsys):
    """A write failure (e.g. read-only filesystem, disk full) must warn to stderr and
    never change the CLI's exit code -- persistence is best-effort telemetry, not the
    CLI's job. Verdicts must still print."""
    monkeypatch.chdir(tmp_path)
    verdicts = _sample_verdicts()
    monkeypatch.setattr(daily, "run_validate", lambda *a, **k: verdicts)

    def _boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(Path, "write_text", _boom)

    today = date(2026, 7, 5)
    rc = _run_validate_cli({"scout": {"validate": {}}}, today=today, lookback_days=365,
                           as_json=False)
    assert rc == 0
    err = capsys.readouterr()
    assert "failed to persist" in err.err
    assert "disk full" in err.err
    # Verdicts still printed on stdout despite the persist failure.
    assert "test:sig" in err.out
    assert not (tmp_path / VALIDATE_LATEST_PATH).exists()


def test_validate_latest_json_round_trips_verdict_keys(tmp_path, monkeypatch):
    """json.dumps(asdict(v)) round-trips through json.loads with every verdict key intact,
    including the tuple fields (alpha_ci, double_sort.spread_ci) which land as JSON arrays."""
    monkeypatch.chdir(tmp_path)
    verdicts = _sample_verdicts()
    monkeypatch.setattr(daily, "run_validate", lambda *a, **k: verdicts)

    today = date(2026, 7, 5)
    _run_validate_cli({"scout": {"validate": {}}}, today=today, lookback_days=365,
                      as_json=False)

    payload = json.loads((tmp_path / VALIDATE_LATEST_PATH).read_text())
    raw, scored = payload["verdicts"]
    assert raw["signal"] == "test:sig"
    assert raw["verdict"] == "HOLD"
    assert raw["cohort_type"] == "raw"
    assert raw["alpha_ci"] == [0.001, 0.02]          # tuple -> list, values preserved
    # B2: n_immature/n_events round-trip -- the pooled old-style fraction stays
    # reconstructable from the persisted JSON alone.
    assert raw["n_immature"] == 2
    assert raw["n_events"] == 12
    assert scored["cohort_type"] == "scored_gated"
    assert scored["n_immature"] == 0
    assert scored["n_events"] == 0
    assert scored["double_sort"]["n_high"] == 3
    assert scored["double_sort"]["spread_ci"] == [0.001, 0.03]


# ---- _load_validation_digest: the digest's read side of the persist boundary (Task 2) ----

def test_load_validation_digest_missing_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _load_validation_digest({}, today=date(2026, 7, 5)) is None


def test_load_validation_digest_malformed_json_returns_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / VALIDATE_LATEST_PATH
    p.parent.mkdir(parents=True)
    p.write_text("not valid json {{{")
    assert _load_validation_digest({}, today=date(2026, 7, 5)) is None


def test_load_validation_digest_not_a_dict_returns_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / VALIDATE_LATEST_PATH
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps([{"signal": "x"}]))
    assert _load_validation_digest({}, today=date(2026, 7, 5)) is None


def test_load_validation_digest_empty_verdicts_returns_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / VALIDATE_LATEST_PATH
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"as_of": "2026-07-05", "source": "live", "verdicts": []}))
    assert _load_validation_digest({}, today=date(2026, 7, 5)) is None


def test_load_validation_digest_missing_as_of_returns_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / VALIDATE_LATEST_PATH
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"source": "live", "verdicts": [{"signal": "x"}]}))
    assert _load_validation_digest({}, today=date(2026, 7, 5)) is None


def test_load_validation_digest_stale_as_of_returns_none_default_14_days(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / VALIDATE_LATEST_PATH
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"as_of": "2026-06-01", "source": "live",
                             "verdicts": [{"signal": "x"}]}))
    # 2026-07-05 - 2026-06-01 = 34 days > default 14 -> stale
    assert _load_validation_digest({}, today=date(2026, 7, 5)) is None


def test_load_validation_digest_fresh_within_default_returns_envelope(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / VALIDATE_LATEST_PATH
    p.parent.mkdir(parents=True)
    payload = {"as_of": "2026-07-01", "source": "live", "verdicts": [{"signal": "x"}]}
    p.write_text(json.dumps(payload))
    # 2026-07-05 - 2026-07-01 = 4 days <= default 14 -> fresh
    result = _load_validation_digest({}, today=date(2026, 7, 5))
    assert result == payload


def test_load_validation_digest_respects_config_max_age_override(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / VALIDATE_LATEST_PATH
    p.parent.mkdir(parents=True)
    payload = {"as_of": "2026-07-01", "source": "live", "verdicts": [{"signal": "x"}]}
    p.write_text(json.dumps(payload))
    cfg = {"scout": {"validate": {"latest_max_age_days": 2}}}
    # 4 days old > configured max of 2 -> stale under the tighter knob
    assert _load_validation_digest(cfg, today=date(2026, 7, 5)) is None


def test_load_validation_digest_exact_boundary_is_fresh(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / VALIDATE_LATEST_PATH
    p.parent.mkdir(parents=True)
    payload = {"as_of": "2026-06-21", "source": "live", "verdicts": [{"signal": "x"}]}
    p.write_text(json.dumps(payload))
    # exactly 14 days old with default max_age=14 -> boundary counts as fresh (<=)
    assert _load_validation_digest({}, today=date(2026, 7, 5)) == payload


def test_load_validation_digest_integrates_with_persisted_cli_output(tmp_path, monkeypatch):
    """End-to-end: `_run_validate_cli` persists, `_load_validation_digest` reads it back."""
    monkeypatch.chdir(tmp_path)
    verdicts = _sample_verdicts()
    monkeypatch.setattr(daily, "run_validate", lambda *a, **k: verdicts)
    today = date(2026, 7, 5)
    _run_validate_cli({"scout": {"validate": {}}}, today=today, lookback_days=365,
                      as_json=False)

    data = _load_validation_digest({}, today=today)
    assert data is not None
    assert data["source"] == "live"
    assert len(data["verdicts"]) == 2


def test_load_validation_digest_null_safe_scout_none(tmp_path, monkeypatch):
    """Config with scout=null (e.g., scout: in YAML) must not crash; use default 14-day max_age."""
    monkeypatch.chdir(tmp_path)
    p = tmp_path / VALIDATE_LATEST_PATH
    p.parent.mkdir(parents=True)
    payload = {"as_of": "2026-07-01", "source": "live", "verdicts": [{"signal": "x"}]}
    p.write_text(json.dumps(payload))
    cfg = {"scout": None}
    # 4 days old <= default 14 -> fresh
    result = _load_validation_digest(cfg, today=date(2026, 7, 5))
    assert result == payload


def test_load_validation_digest_null_safe_validate_none(tmp_path, monkeypatch):
    """Config with scout.validate=null must not crash; use default 14-day max_age."""
    monkeypatch.chdir(tmp_path)
    p = tmp_path / VALIDATE_LATEST_PATH
    p.parent.mkdir(parents=True)
    payload = {"as_of": "2026-07-01", "source": "live", "verdicts": [{"signal": "x"}]}
    p.write_text(json.dumps(payload))
    cfg = {"scout": {"validate": None}}
    # 4 days old <= default 14 -> fresh
    result = _load_validation_digest(cfg, today=date(2026, 7, 5))
    assert result == payload


def test_suppressed_verdict_survives_the_persist_round_trip_with_no_leaked_level(
        tmp_path, monkeypatch):
    """R-0f end to end: a floor-suppressed verdict must reach the digest with the flag
    intact, every level field null, and NO absolute double-sort leg -- the persisted
    artifact is the surface audits actually get written from."""
    monkeypatch.chdir(tmp_path)
    from shortlist.scout.validate import attach_double_sort

    v = attach_double_sort(
        SignalVerdict(signal="test:sig", verdict="INSUFFICIENT", ir=None,
                      alpha_monthly=None, alpha_ci=None, effective_blocks=6,
                      n_selected=100, n_measurable=92, measurable_fraction=0.92,
                      sensitivity_flip=False, cohort_type="scored_gated",
                      alpha_suppressed=True),
        {"n_high": 20, "n_low": 22, "months": 30, "effective_blocks": 6,
         "spread_alpha_monthly": 0.0244, "spread_ci": (0.001, 0.048),
         "high_ir": 1.0438, "low_ir": 0.4956})
    monkeypatch.setattr(daily, "run_validate", lambda *a, **k: [v])

    today = date(2026, 7, 5)
    _run_validate_cli({"scout": {"validate": {}}}, today=today, lookback_days=365,
                      as_json=False)

    data = _load_validation_digest({}, today=today)
    got = data["verdicts"][0]
    assert got["alpha_suppressed"] is True
    assert got["ir"] is None and got["alpha_monthly"] is None and got["alpha_ci"] is None
    assert got["double_sort"]["high_ir"] is None
    assert got["double_sort"]["low_ir"] is None
    assert got["double_sort"]["spread_alpha_monthly"] == 0.0244      # the spread survives
    # Nothing anywhere in the artifact reads as a quotable per-cohort level.
    assert "1.0438" not in json.dumps(got)
