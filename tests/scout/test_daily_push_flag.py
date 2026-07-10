from datetime import date
from shortlist.scout import daily
import contextlib


def test_daily_push_disabled_returns_zero_before_scanning(capsys, monkeypatch):
    # Prove the no-op happens BEFORE any scanning: make the first thing run() would
    # touch after the flag check explode. If the flag check is correctly placed
    # first, run() returns 0 without ever calling it.
    def boom(*a, **k):
        raise AssertionError("run() proceeded past the flag check")
    monkeypatch.setattr(daily, "configure_default_cache", boom, raising=False)
    monkeypatch.setattr(daily, "build_signals", boom, raising=False)

    cfg = {"scout": {"daily_push": {"enabled": False}}}
    rc = daily.run(cfg, demo=False, today=date(2026, 6, 6))
    assert rc == 0
    assert "disabled" in capsys.readouterr().out.lower()


def test_daily_push_absent_flag_defaults_disabled():
    # No daily_push block at all -> treated as disabled (safe default).
    cfg = {"scout": {}}
    assert daily.run(cfg, demo=False, today=date(2026, 6, 6)) == 0


def test_demo_bypasses_daily_push_flag(monkeypatch):
    # demo=True must proceed past the flag (it's the offline smoke path). Prove it
    # by asserting run() gets past the check into the demo body (reaches build_signals).
    reached = {"v": False}
    def mark(*a, **k):
        reached["v"] = True
        raise RuntimeError("stop after proving we got here")
    monkeypatch.setattr(daily, "build_signals", mark, raising=False)
    with contextlib.suppress(RuntimeError):
        daily.run({"scout": {}}, demo=True, today=date(2026, 6, 6))
    assert reached["v"] is True
