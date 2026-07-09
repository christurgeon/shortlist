"""Buyback backfill leg: assemble_buyback_events purity, the _BACKFILL_SPECS row resolves,
the prereg YAML loads + pins, and CLI routing. Mirrors test_scout_backfill_eightk.py's
injected-seam idiom — no network."""
from datetime import date

from shortlist.scout import backfill, daily
from shortlist.scout.backfill import (_BACKFILL_SPECS, assemble_buyback_events,
                                      run_backfill_buyback)
from shortlist.scout.buyback import SIGNAL as SIGNAL_BUYBACK
from shortlist.scout.daily import _DEFAULT_CONFIG, build_arg_parser
from shortlist.scout.preregister import load_prereg

_REPO_ROOT = str(_DEFAULT_CONFIG.parent)


def _row(adsh, cik="0000000007", phrase="approved a new share repurchase program",
         file_date="2023-10-13", file_type="8-K", items=("8.01",), sics=("3571",),
         names=("Real Business Inc",)):
    return {"adsh": adsh, "cik": cik, "phrase": phrase, "file_date": file_date,
            "file_type": file_type, "items": list(items), "sics": list(sics),
            "display_names": list(names)}


def _resolve(mapping):
    return lambda cik, as_of: mapping.get(cik)


R7 = _resolve({"0000000007": "RBI"})


# --- assemble_buyback_events (pure) ---

def test_assemble_happy_path_shape_key_entry_phrase_and_sic():
    evs = assemble_buyback_events([_row("a-1")], R7, signal=SIGNAL_BUYBACK)
    assert len(evs) == 1
    e = evs[0]
    assert e.signal == SIGNAL_BUYBACK and e.ticker == "RBI" and e.cik == "0000000007"
    assert e.origin == "backfill" and e.strength == 0.6
    assert e.event_date == date(2023, 10, 16)             # F12: Fri filing -> Mon entry
    assert e.meta["filing_date"] == "2023-10-13"
    assert e.meta["key"] == f"{SIGNAL_BUYBACK}|0000000007|2023-10-13"
    assert e.meta["adsh"] == "a-1"
    assert e.meta["phrase"] == "approved a new share repurchase program"
    assert e.meta["sic"] == "3571"                        # EFTS sic reused — no fetch later


def test_assemble_excludes_amendment_and_cross_phrase_dedup():
    rows = [_row("a-1", file_type="8-K/A"),               # root_forms leak: excluded FIRST
            _row("dup", phrase="approved a share repurchase program"),
            _row("dup", phrase="authorized a share repurchase program")]  # same accession
    evs = assemble_buyback_events(rows, R7, signal=SIGNAL_BUYBACK)
    assert [e.meta["adsh"] for e in evs] == ["dup"]       # one event per accession


def test_assemble_quality_drops_are_exclusions():
    rows = [_row("a-1", cik="1", sics=("6770",)),                    # blank-check SIC
            _row("a-2", cik="2", names=("Peace Acquisition Corp",)),  # SPAC name
            _row("a-3", cik="3")]                                    # -> ABCDF junk suffix
    resolver = _resolve({"1": "AAA", "2": "BBB", "3": "ABCDF"})
    assert assemble_buyback_events(rows, resolver, signal=SIGNAL_BUYBACK) == []


def test_assemble_unresolved_is_sentinel_selected_not_dropped():
    evs = assemble_buyback_events([_row("a-1")], _resolve({}), signal=SIGNAL_BUYBACK)
    assert len(evs) == 1
    assert evs[0].ticker == "CIK:0000000007"
    assert evs[0].meta["non_measurable_hint"] == "unresolved_ticker"


def test_assemble_resolver_called_with_filing_date_not_entry():
    seen = []

    def resolver(cik, as_of):
        seen.append(as_of)
        return "RBI"
    assemble_buyback_events([_row("a-1")], resolver, signal=SIGNAL_BUYBACK)
    assert seen == [date(2023, 10, 13)]                   # PiT at FILING date (F12 guard)


# --- spec-table + prereg ---

def test_backfill_spec_row_resolves():
    spec = _BACKFILL_SPECS["buyback"]
    assert spec["signal"] == SIGNAL_BUYBACK == "edgar:buyback_auth"
    assert spec["slug"] == "edgar_buyback"
    assert callable(spec["assemble"]) and callable(spec["fetch_factory"])


def test_prereg_edgar_buyback_pins():
    p = load_prereg("edgar_buyback", repo_root=_REPO_ROOT)
    assert p["signal"] == "edgar:buyback_auth"
    assert p["window_start"] == date(2022, 1, 1) and p["window_end"] == date(2025, 12, 31)
    assert p["k_months"] == 3
    assert p["min_measurable_frac"] == 0.90
    assert p["min_independent_blocks"] == 8
    assert p["verdict_as_of"] == p["as_of"] == date(2026, 7, 9)


# --- run_backfill via the spec table + CLI routing ---

class _FakeSym:
    low_confidence: list = []
    disagreements: list = []

    def resolve_ticker(self, cik, as_of):
        return {"0000000007": "RBI"}.get(cik)

    def close(self):
        pass


def test_run_backfill_buyback_loads_prereg_by_slug(monkeypatch, tmp_path):
    seen = []

    def fake_load(slug, *, repo_root):
        seen.append(slug)
        return {"k_months": 3}

    monkeypatch.setattr("shortlist.scout.preregister.load_prereg", fake_load)
    cfg = {"scout": {"backfill": {"sec_throttle_s": 0.0, "yahoo_throttle_s": 0.0}}}
    run_backfill_buyback(cfg, start=date(2023, 6, 1), end=date(2023, 6, 30),
                         identity="t@example.com", today=date(2026, 7, 9),
                         out_path=str(tmp_path / "bb.jsonl"),
                         _fetch_window=lambda *a, **k: [], _symbology=_FakeSym(),
                         _fetch_history=lambda t: None, _fetch_delisting=lambda c: [],
                         _free_gb=lambda p: 50.0)
    assert seen == ["edgar_buyback"]


def test_cli_choice_accepts_buyback_and_routes(monkeypatch):
    parser = build_arg_parser()
    ns = parser.parse_args(["backfill", "--signal", "buyback", "--start", "2022-01-01",
                            "--end", "2022-01-31"])
    assert ns.signal == "buyback"

    monkeypatch.setenv("SEC_IDENTITY", "t@example.com")
    calls = []
    monkeypatch.setattr("shortlist.scout.backfill.run_backfill_buyback",
                        lambda config, **kw: calls.append("buyback") or {"n_selected": 0})
    rc = daily._run_backfill_cli({"scout": {}}, signal="buyback", start=date(2022, 1, 1),
                                 end=date(2022, 1, 31), out_path=None, as_json=True)
    assert rc == 0 and calls == ["buyback"]
