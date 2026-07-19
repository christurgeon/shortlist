"""13D/A stake-increase backfill leg (Task 8): the run-level stateful assembler is the
core (chronological baseline map across chunks — first-sighting amendments seed-and-
never-emit, parse abstention is a selection exclusion, unresolved-but-qualified tickers
are selected CIK: sentinels), plus one test each for the historical walker
(fetch_amendment_window) and the CLI --signal choice. Mirrors
tests/test_scout_backfill_buyback.py's injected-seam idiom — no network."""
from datetime import date
from types import SimpleNamespace

from shortlist.scout import daily
from shortlist.scout.backfill import _BACKFILL_SPECS, _assemble_13d_a_factory
from shortlist.scout.daily import _DEFAULT_CONFIG, build_arg_parser
from shortlist.scout.preregister import load_prereg
from shortlist.scout.stake import SIGNAL as STAKE_SIGNAL

_REPO_ROOT = str(_DEFAULT_CONFIG.parent)


def _rec(form="SCHEDULE 13D/A", pct=8.0, fdate=date(2023, 3, 10), acc="a1",
         filer="0000000900", subj="0000000123"):
    return {"cik": subj, "filer_cik": filer, "subject_name": "Target Co",
            "activist": "Fund LP", "form": form, "accession": acc,
            "filing_date": fdate, "stake_pct": pct}


# --- _assemble_13d_a_factory (pure, run-level stateful) ---

def test_spec_row_registered():
    row = _BACKFILL_SPECS["13d-a"]
    assert row["signal"] == "edgar:13d_stake_increase"
    assert row["slug"] == "edgar_13d_stake_increase"
    assert "assemble_factory" in row
    assert callable(row["fetch_factory"])


def test_initial_seeds_amendment_emits_across_chunks():
    asm = _assemble_13d_a_factory({}, date(2026, 7, 17))
    # chunk 1: initial 13D at 5.0% -> seeds, no event
    ev1 = asm([_rec(form="SCHEDULE 13D", pct=5.0, fdate=date(2023, 1, 5), acc="i1")],
              lambda cik, d: "TGT")
    assert ev1 == []
    # chunk 2 (later month): amendment to 8.0% -> +3.0pp >= 2.0 -> selected event
    ev2 = asm([_rec(pct=8.0, fdate=date(2023, 3, 10), acc="a1")], lambda cik, d: "TGT")
    assert len(ev2) == 1
    e = ev2[0]
    assert e.signal == "edgar:13d_stake_increase" and e.ticker == "TGT"
    assert e.origin == "backfill" and e.strength == 0.6
    assert e.meta["prior_pct"] == 5.0 and e.meta["new_pct"] == 8.0
    assert e.meta["key"].startswith("edgar:13d_stake_increase|")
    assert e.event_date > date(2023, 3, 10)          # next trading day, never filing day


def test_first_sighting_seeds_only_and_immaterial_excluded():
    asm = _assemble_13d_a_factory({}, date(2026, 7, 17))
    assert asm([_rec(pct=6.0, acc="a1")], lambda c, d: "TGT") == []      # seed-only
    assert asm([_rec(pct=7.0, acc="a2", fdate=date(2023, 4, 1))],
               lambda c, d: "TGT") == []                                  # +1.0 < 2.0
    got = asm([_rec(pct=9.5, acc="a3", fdate=date(2023, 5, 1))], lambda c, d: "TGT")
    assert len(got) == 1 and got[0].meta["prior_pct"] == 7.0             # vs LATEST baseline


def test_unresolved_ticker_is_selected_sentinel():
    asm = _assemble_13d_a_factory({}, date(2026, 7, 17))
    asm([_rec(form="SCHEDULE 13D", pct=5.0, acc="i1", fdate=date(2023, 1, 5))],
        lambda c, d: None)
    ev = asm([_rec(pct=8.0, acc="a1")], lambda c, d: None)
    assert len(ev) == 1 and ev[0].ticker == "CIK:0000000123"             # non-measurable
    assert ev[0].meta["non_measurable_hint"] == "unresolved_ticker"


def test_unparsed_stake_is_exclusion_not_sentinel():
    asm = _assemble_13d_a_factory({}, date(2026, 7, 17))
    asm([_rec(form="SCHEDULE 13D", pct=5.0, acc="i1", fdate=date(2023, 1, 5))],
        lambda c, d: "TGT")
    assert asm([_rec(pct=None, acc="a1")], lambda c, d: "TGT") == []     # never selected


def test_unresolved_pair_key_is_exclusion_not_sentinel():
    """A missing filer_cik/subject cik (header failure upstream) can't even be paired to a
    baseline -- abstention, excluded entirely, distinct from the unresolved-TICKER sentinel
    path above (which only fires once a real delta already qualified)."""
    asm = _assemble_13d_a_factory({}, date(2026, 7, 17))
    asm([_rec(form="SCHEDULE 13D", pct=5.0, acc="i1", fdate=date(2023, 1, 5))],
        lambda c, d: "TGT")
    bad = _rec(pct=9.0, acc="a1")
    bad["filer_cik"] = None
    assert asm([bad], lambda c, d: "TGT") == []


def test_spac_and_affiliate_rows_excluded_never_seed_or_emit():
    asm = _assemble_13d_a_factory({}, date(2026, 7, 17))
    spac = _rec(form="SCHEDULE 13D", pct=5.0, acc="i1", fdate=date(2023, 1, 5))
    spac["subject_name"] = "Peace Acquisition Corp"
    assert asm([spac], lambda c, d: "TGT") == []
    affiliate = _rec(form="SCHEDULE 13D", pct=5.0, acc="i2", fdate=date(2023, 1, 6))
    affiliate["subject_name"] = "Hawkeye Systems"
    affiliate["activist"] = "Hawkeye HoldCo LLC"
    assert asm([affiliate], lambda c, d: "TGT") == []
    # neither seeded a baseline: a later material "amendment" on the SAME pair key (same
    # subject/filer CIKs) but a NON-SPAC subject name isn't excluded by the SPAC check --
    # if either prior row had seeded a baseline, this pct (9.0 vs the spac row's 5.0, a
    # +4.0pp delta) would clear MIN_INCREASE_PP and emit. It doesn't, proving the SPAC row
    # never seeded (this is the first sighting for this pair, so it seeds-only).
    later = _rec(pct=9.0, acc="a3", fdate=date(2023, 6, 1))
    later["subject_name"] = "Peace Industries Inc"
    assert asm([later], lambda c, d: "TGT") == []


def test_two_filers_same_subject_same_day_amendments_both_emit_with_distinct_keys():
    """Regression: the dedup key must be accession-first, not subject-CIK-first. Two
    DISTINCT filers escalating the SAME subject on the SAME day previously collided under
    one f"{signal}|{cik}|{date}" key (append_events dedups by meta['key']), silently
    dropping the second filer's event."""
    asm = _assemble_13d_a_factory({}, date(2026, 7, 17))
    # seed both pairs via two initial 13Ds (same subject, distinct filers)
    asm([
        _rec(form="SCHEDULE 13D", pct=5.0, fdate=date(2023, 1, 5), acc="i1",
             filer="0000000900", subj="0000000123"),
        _rec(form="SCHEDULE 13D", pct=5.0, fdate=date(2023, 1, 5), acc="i2",
             filer="0000000901", subj="0000000123"),
    ], lambda c, d: "TGT")
    # same-day, same-subject amendments from both filers -- both material increases
    ev = asm([
        _rec(pct=8.0, fdate=date(2023, 3, 10), acc="a1", filer="0000000900",
             subj="0000000123"),
        _rec(pct=8.0, fdate=date(2023, 3, 10), acc="a2", filer="0000000901",
             subj="0000000123"),
    ], lambda c, d: "TGT")
    assert len(ev) == 2
    keys = {e.meta["key"] for e in ev}
    assert len(keys) == 2                                 # distinct keys -- no collision


def test_in_chunk_records_are_sorted_by_filing_date_regardless_of_list_order():
    """A single chunk's records list can arrive out of date order (no ordering guarantee
    from the caller) -- the assembler's internal sorted() by filing_date must still process
    the initial before the later amendment."""
    asm = _assemble_13d_a_factory({}, date(2026, 7, 17))
    amendment = _rec(pct=8.0, fdate=date(2023, 3, 10), acc="a1")
    initial = _rec(form="SCHEDULE 13D", pct=5.0, fdate=date(2023, 1, 5), acc="i1")
    ev = asm([amendment, initial], lambda c, d: "TGT")    # amendment listed BEFORE initial
    assert len(ev) == 1 and ev[0].meta["prior_pct"] == 5.0


def test_min_increase_pp_is_the_code_constant_not_config():
    """bf carries a config dict but the assembler never reads a min_increase_pp override
    from it -- stake.MIN_INCREASE_PP is frozen in code (the buyback DEFAULT_PHRASES
    precedent)."""
    asm = _assemble_13d_a_factory({"min_increase_pp": 100.0}, date(2026, 7, 17))
    asm([_rec(form="SCHEDULE 13D", pct=5.0, acc="i1", fdate=date(2023, 1, 5))],
        lambda c, d: "TGT")
    got = asm([_rec(pct=8.0, acc="a1")], lambda c, d: "TGT")
    assert len(got) == 1                              # a 100pp config override is ignored


# --- prereg YAML ---

def test_prereg_edgar_13d_stake_increase_pins():
    p = load_prereg("edgar_13d_stake_increase", repo_root=_REPO_ROOT)
    assert p["signal"] == "edgar:13d_stake_increase" == STAKE_SIGNAL
    assert p["window_start"] == date(2022, 1, 1) and p["window_end"] == date(2025, 12, 31)
    assert p["k_months"] == 3
    assert p["min_measurable_frac"] == 0.90
    assert p["min_independent_blocks"] == 8
    assert p["verdict_as_of"] == p["as_of"]


def test_backfill_spec_slugs_match_validate_slug_derivation_includes_13da():
    """Same invariant tests/test_scout_backfill_buyback.py pins for every row -- confirm
    it holds for the new one without touching that shared test."""
    from shortlist.scout.daily import _slug_for_signal
    row = _BACKFILL_SPECS["13d-a"]
    assert row["slug"] == _slug_for_signal(row["signal"])
    assert load_prereg(row["slug"], repo_root=_REPO_ROOT)


# --- CLI ---

def test_cli_accepts_13d_a():
    p = build_arg_parser()
    ns = p.parse_args(["backfill", "--signal", "13d-a", "--start", "2022-01-01",
                       "--end", "2025-12-31"])
    assert ns.signal == "13d-a"


def test_cli_routes_13d_a_to_run_backfill_13d_a(monkeypatch):
    monkeypatch.setenv("SEC_IDENTITY", "t@example.com")
    calls = []
    monkeypatch.setattr("shortlist.scout.backfill.run_backfill_13d_a",
                        lambda config, **kw: calls.append("13d-a") or {"n_selected": 0})
    rc = daily._run_backfill_cli({"scout": {}}, signal="13d-a", start=date(2022, 1, 1),
                                 end=date(2025, 12, 31), out_path=None, as_json=True)
    assert rc == 0 and calls == ["13d-a"]


# --- fetch_amendment_window (historical walker) ---

class _CI:
    def __init__(self, cik, name):
        self.cik = cik
        self.name = name


class _Hdr:
    def __init__(self, subject_cik, subject_name, filer_cik, filer_name):
        self.subject_companies = [SimpleNamespace(
            company_information=_CI(subject_cik, subject_name))]
        self.filers = [SimpleNamespace(company_information=_CI(filer_cik, filer_name))]


class _Filing:
    def __init__(self, form, fdate, acc, subject_cik=886158, subject_name="Target Corp",
                 filer_cik=900, filer_name="Fund LP"):
        self.form = form
        self.filing_date = fdate
        self.accession_no = acc
        self.header = _Hdr(subject_cik, subject_name, filer_cik, filer_name)

    def xml(self):
        return None

    def text(self):
        return "irrelevant"


def _fake_get_filings(rows):
    def fake(form, filing_date):
        return [r for r in rows if r.form == form]
    return fake


def test_walker_keeps_initial_and_amendment_parses_filer_cik_and_stake_and_warns_rate():
    import pytest

    from shortlist.backtest.edgar_history import fetch_amendment_window

    rows = [
        _Filing("SCHEDULE 13D", date(2023, 1, 5), "i-1"),
        _Filing("SCHEDULE 13D/A", date(2023, 3, 10), "a-1"),
        _Filing("SCHEDULE 13D/G", date(2023, 3, 11), "g-1"),   # not 13D/13D-A -> excluded
    ]
    fake = _fake_get_filings(rows)
    seen_filings = []

    def _stake_fn(filing):
        seen_filings.append(filing)
        return 8.0

    with pytest.warns(UserWarning, match="stake parse rate 2/2"):
        recs = fetch_amendment_window(date(2023, 1, 1), date(2023, 3, 31), "t@example.com",
                                      throttle_s=0.0, _get_filings=fake, _stake_fn=_stake_fn)
    assert [r["accession"] for r in recs] == ["i-1", "a-1"]
    assert recs[0]["cik"] == "0000886158" and recs[0]["filer_cik"] == "0000000900"
    assert recs[0]["stake_pct"] == 8.0 and recs[1]["stake_pct"] == 8.0
    assert len(seen_filings) == 2


def test_walker_index_failure_returns_none_and_warns():
    import pytest

    from shortlist.backtest.edgar_history import fetch_amendment_window

    def boom(form, filing_date):
        raise RuntimeError("EDGAR down")

    with pytest.warns(UserWarning, match="edgar_history"):
        assert fetch_amendment_window(date(2023, 1, 1), date(2023, 1, 2), "t@example.com",
                                      throttle_s=0.0, _get_filings=boom) is None


def test_walker_skips_stake_doc_fetch_for_spac_and_affiliate_rows():
    """Controller-resolution guard: rows the assembler would exclude anyway (SPAC/shell
    subject, or an affiliate filer) never spend a doc-fetch -- stake_pct stays None, and
    the seam function is never called for them."""
    from shortlist.backtest.edgar_history import fetch_amendment_window

    rows = [
        _Filing("SCHEDULE 13D", date(2023, 1, 5), "spac-1",
               subject_name="Peace Acquisition Corp"),
        _Filing("SCHEDULE 13D", date(2023, 1, 6), "aff-1",
               subject_name="Hawkeye Systems", filer_name="Hawkeye HoldCo LLC"),
        _Filing("SCHEDULE 13D", date(2023, 1, 7), "ok-1"),
    ]
    fake = _fake_get_filings(rows)
    called = []

    def _stake_fn(filing):
        called.append(filing.accession_no)
        return 5.0

    recs = fetch_amendment_window(date(2023, 1, 1), date(2023, 1, 31), "t@example.com",
                                  throttle_s=0.0, _get_filings=fake, _stake_fn=_stake_fn)
    by_acc = {r["accession"]: r for r in recs}
    assert by_acc["spac-1"]["stake_pct"] is None
    assert by_acc["aff-1"]["stake_pct"] is None
    assert by_acc["ok-1"]["stake_pct"] == 5.0
    assert called == ["ok-1"]                          # the seam is never spent on the other two
