"""A backtest must refuse to run on a universe carrying dead symbols.

Measured 2026-08-15: 8 of 238 tickers in the committed universes no longer resolved
(4 renamed their symbol, 4 stopped filing). A stale symbol is not inert — it errors
and contributes nothing, so the cross-section silently shrinks against the ~30-name
IC trust floor, and nobody notices for a year. The cost lands on the measurement, so
the check belongs at the moment of measurement.

Known limitation, deliberate: this catches symbols that VANISH from SEC's map. It
cannot catch a symbol REASSIGNED to a different issuer (ticker `B` -> Barrick Mining),
because that still resolves. Only pinning CIKs would.
"""
import pytest

from shortlist.backtest.universe import stale_tickers

KNOWN = {"AAPL": "0000320193", "MSFT": "0000789019", "MRSH": "0000062709"}


def test_flags_only_symbols_absent_from_secs_map():
    assert stale_tickers(["AAPL", "MMC", "MSFT", "AMED"], KNOWN) == ["MMC", "AMED"]


def test_all_known_is_empty():
    assert stale_tickers(["AAPL", "MSFT"], KNOWN) == []


def test_is_case_insensitive_and_order_preserving():
    assert stale_tickers(["msft", "zzz", "aapl", "yyy"], KNOWN) == ["ZZZ", "YYY"]


@pytest.mark.parametrize("index", [None, {}])
def test_an_unavailable_sec_map_abstains_rather_than_failing_every_ticker(index):
    """SEC being down must never block a backtest. An empty/None index means 'we do
    not know', which is not the same as 'every ticker is dead' — the repo's
    abstain-never-block pattern (nasdaq_universe returns {} on failure)."""
    assert stale_tickers(["AAPL", "MMC"], index) == []


def test_a_stale_universe_aborts_the_run_and_names_the_offenders(monkeypatch, capsys):
    from shortlist.backtest import cli
    monkeypatch.setattr(cli, "_known_symbols", lambda cache_dir: KNOWN, raising=False)
    monkeypatch.setattr(cli, "load_universe", lambda spec: ["AAPL", "MMC", "AMED"], raising=False)
    rc = cli.main(["--universe", "largecap", "--source", "xbrl", "--horizons", "12"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "MMC" in err and "AMED" in err
    assert "renamed" in err or "delisted" in err
    assert "--allow-stale-universe" in err          # tells the user how to proceed


def test_the_escape_hatch_lets_a_deliberate_run_through(monkeypatch):
    """Getting past the guard must not require editing the committed universe."""
    from shortlist.backtest import cli
    monkeypatch.setattr(cli, "_known_symbols", lambda cache_dir: KNOWN, raising=False)
    called = {}
    monkeypatch.setattr(cli, "load_universe", lambda spec: ["AAPL", "MMC"], raising=False)

    def _stop(*a, **k):
        called["ran"] = True
        raise SystemExit(99)                        # past the guard; stop cheaply
    monkeypatch.setattr(cli, "_load_histories", _stop, raising=False)
    with pytest.raises(SystemExit):
        cli.main(["--universe", "largecap", "--allow-stale-universe", "--horizons", "12"])
    assert called.get("ran") is True


def test_an_ad_hoc_ticker_list_is_NOT_guarded(monkeypatch):
    """`--tickers T00,T01` is the caller's deliberate choice — synthetic and delisted
    symbols are legitimate there. Guarding it also dragged a network call into every
    offline CLI test, which is how this scoping bug was caught."""
    from shortlist.backtest import cli
    called = {}
    monkeypatch.setattr(cli, "_known_symbols",
                        lambda cache_dir: called.setdefault("fetched", True) or KNOWN,
                        raising=False)

    def _stop(*a, **k):
        raise SystemExit(99)
    monkeypatch.setattr(cli, "_load_histories", _stop, raising=False)
    with pytest.raises(SystemExit):
        cli.main(["--tickers", "T00,T01", "--horizons", "12"])
    assert "fetched" not in called, "the guard must not touch the network for an ad-hoc list"
