from datetime import date

import yaml

from shortlist.scout import daily
from shortlist.scout.daily import build_arg_parser, run_validate   # thin helpers for testability


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
