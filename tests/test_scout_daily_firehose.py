from datetime import date
from shortlist.scout.daily import _log_firehose   # thin helper we add for testability
from shortlist.scout.models import Emission
from shortlist.scout.state import ScoutState


def _ems():
    return [Emission("ABC", "edgar:activist_13d", 0.9, "ev", True, "0000123"),
            Emission("XYZ", "finra:short_interest", 0.4, "ev", True, None)]


def test_log_firehose_records_all_emissions_when_enabled(tmp_path):
    st = ScoutState(tmp_path / "s.json")
    _log_firehose(st, _ems(), date(2026, 7, 1), {"firehose": {"enabled": True}})
    rows = st.firehose_events(on=date(2026, 7, 1), lookback_days=30)
    assert {r["ticker"] for r in rows} == {"ABC", "XYZ"}


def test_log_firehose_noop_when_disabled(tmp_path):
    st = ScoutState(tmp_path / "s.json")
    _log_firehose(st, _ems(), date(2026, 7, 1), {})          # no firehose key -> off
    assert st.firehose_events(on=date(2026, 7, 1), lookback_days=30) == []


def test_log_firehose_best_effort_swallows_errors(tmp_path):
    class Boom(ScoutState):
        def record_firehose(self, events, session):
            raise RuntimeError("disk full")
    st = Boom(tmp_path / "s.json")
    # Must NOT raise — best-effort logging never aborts a run.
    _log_firehose(st, _ems(), date(2026, 7, 1), {"firehose": {"enabled": True}})


def test_log_firehose_respects_max_cap(tmp_path):
    st = ScoutState(tmp_path / "s.json")
    many = [Emission(f"T{i}", "finra:short_interest", 0.4, "ev", True, None) for i in range(5)]
    _log_firehose(st, many, date(2026, 7, 1), {"firehose": {"enabled": True, "max_events_per_run": 3}})
    assert len(st.firehose_events(on=date(2026, 7, 1), lookback_days=30)) == 3
