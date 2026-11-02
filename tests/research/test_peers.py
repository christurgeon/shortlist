"""The "Peer comparison" section: peer filtering, the industry fallback, size selection, sector
masks, median and rank, the two derived lines, and fetch failures that read as "unavailable"."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from shortlist.research import peers
from shortlist.research.models import PeerTable

NOW = datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc)
CFG = {"research": {"peers": {"enabled": True, "min_interval_s": 0, "timeout": 5}},
       "gates": {"min_market_cap": 3.0e8}}


def _m(cap, **over):
    """A metric body's fields in vendor units ($M, percent)."""
    base = {"marketCapitalization": cap, "peTTM": 20.0, "evEbitdaTTM": 12.0, "psTTM": 2.0,
            "pb": 3.0, "grossMarginTTM": 40.0, "operatingMarginTTM": 10.0,
            "revenueGrowthTTMYoy": 5.0, "revenueGrowth3Y": 4.0, "roiTTM": 12.0, "roeTTM": 15.0,
            "totalDebt/totalEquityQuarterly": 0.5, "52WeekPriceReturnDaily": 10.0}
    base.update(over)
    return base


class _Vendor:
    """Finnhub routed by path and symbol. A value that is an int is an HTTP status."""

    def __init__(self, peer_lists, metrics, spy=11.0):
        self.peer_lists, self.metrics, self.spy = peer_lists, metrics, spy
        self.requests: list[httpx.Request] = []

    def __call__(self, request):
        self.requests.append(request)
        p = request.url.params
        if request.url.path.endswith("/stock/peers"):
            body = self.peer_lists.get(p.get("grouping", "default"), [])
        elif p["symbol"] == "SPY":
            body = {"symbol": "SPY", "metric": {"52WeekPriceReturnDaily": self.spy}}
        else:
            m = self.metrics.get(p["symbol"])
            body = {"symbol": p["symbol"], "metric": m} if isinstance(m, dict) else m
        if isinstance(body, int):
            return httpx.Response(body, json={})
        if isinstance(body, Exception):
            raise body
        return httpx.Response(200, json=body if body is not None else {"metric": {}})

    def paths(self):
        return [(r.url.path.rsplit("/", 1)[-1], r.url.params.get("symbol"),
                 r.url.params.get("grouping")) for r in self.requests]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(peers, "_spy_cache", {})
    monkeypatch.setenv("FINNHUB_API_KEY", "k-123")


def _fetch(vendor, bucket=None, cfg=CFG):
    card = SimpleNamespace(ticker="ACME", sic_bucket=bucket)
    return peers.fetch_section(card, cfg, now=NOW, transport=httpx.MockTransport(vendor))


def _five():
    return {"ACME": _m(10_000), "AAA": _m(20_000), "BBB": _m(8_000), "CCC": _m(5_000),
            "DDD": _m(2_000)}


# ---- filtering ----

def test_the_peer_list_drops_self_duplicates_and_junk():
    body = ["ACME", "AAA", "aaa", "BBB", "BBB", "", None, "x y", "<b>", "BRK.B"]
    assert peers.peer_list(body, "acme") == ["AAA", "BBB", "BRK.B"]
    with pytest.raises(ValueError):
        peers.peer_list({"error": "x"}, "ACME")


def test_a_metric_body_without_fields_or_for_another_symbol_is_none():
    assert peers.metrics_from({"symbol": "X", "metric": {}}, "X") is None
    assert peers.metrics_from({"symbol": "Y", "metric": _m(1)}, "X") is None
    assert peers.metrics_from({"metric": {"peTTM": float("nan")}}, "X") is None
    assert peers.metrics_from({"symbol": "X", "metric": _m(1)}, "X")["marketCapitalization"] == 1


def test_micro_caps_failed_metrics_and_share_classes_are_not_usable():
    target = _m(10_000, revenueGrowthTTMYoy=7.0)
    cands = [("TINY", _m(0.2)), ("GONE", None), ("ACMEB", _m(10_000, revenueGrowthTTMYoy=7.0)),
             ("AAA", _m(900)), ("AAAB", _m(900)), ("BBB", _m(300))]
    assert [t for t, _ in peers.usable(cands, target, 300.0)] == ["AAA", "BBB"]


def test_peers_are_kept_by_closeness_in_log_market_cap_and_shown_largest_first():
    ps = [(t, _m(c)) for t, c in [("HUGE", 1_000_000), ("TWICE", 20_000), ("HALF", 5_000),
                                  ("TENTH", 1_000), ("SAME", 10_000)]]
    kept = peers.closest_in_size(ps, 10_000, 3)
    assert [t for t, _ in kept] == ["TWICE", "SAME", "HALF"]
    assert [t for t, _ in peers.closest_in_size(ps, None, 2)] == ["HUGE", "TWICE"]


def test_the_floor_follows_the_gate_unless_the_block_sets_its_own():
    assert peers.market_cap_floor_m({"gates": {"min_market_cap": 5e8}}, {}) == 500.0
    assert peers.market_cap_floor_m({"gates": {}}, {"min_market_cap": 1e9}) == 1000.0
    assert peers.market_cap_floor_m(None, {}) == 300.0


# ---- median and rank ----

def test_median_and_rank_use_only_peers_that_report_the_measure():
    col = peers._BY_KEY["pe"]
    r = peers.row(col, 15.0, [10.0, None, 20.0, 30.0])
    assert (r.company, r.median, r.n, r.rank) == ("15.0x", "20.0x", 3, "2nd lowest of 4")
    assert peers.row(col, 5.0, [10.0]).rank == "lowest of 2"
    assert peers.row(peers._BY_KEY["gross_margin"], 50.0, [10.0, 60.0]).rank == "2nd highest of 3"
    assert peers.row(peers._BY_KEY["market_cap"], 9.0, [1.0]).rank == "largest of 2"


def test_ties_share_the_better_rank_and_a_missing_company_is_not_ranked():
    col = peers._BY_KEY["roi"]
    assert peers.row(col, 10.0, [10.0, 10.0, 5.0]).rank == "highest of 4"
    r = peers.row(col, None, [1.0, 2.0])
    assert (r.company, r.median, r.rank) == ("—", "1.5%", "")
    assert peers.row(col, None, []).median == "—"


def test_a_negative_multiple_is_not_meaningful_but_zero_debt_is_real():
    pe = peers.row(peers._BY_KEY["pe"], -12.0, [10.0, -5.0, 0.0, 20.0])
    assert (pe.company, pe.median, pe.n, pe.rank) == ("n/m", "15.0x", 2, "")
    de = peers.row(peers._BY_KEY["debt_to_equity"], 0.0, [0.5, -1.0])
    assert (de.company, de.n, de.rank) == ("0.00", 1, "lowest of 2")


def test_formats():
    assert [peers.fmt("usd_m", v) for v in (181.4, 57_436.9, 3_422_443.2)] == [
        "$181M", "$57.4B", "$3.42T"]
    assert peers.fmt("signed_pct", -1.18) == "-1.2%" and peers.fmt("ratio", None) == "—"


# ---- the derived lines ----

def test_relative_performance_names_the_larger_part_of_the_move():
    line = peers.relative_line("ACME", -40.0, [-10.0, -5.0, 0.0], 10.0, 3)
    assert "peer median -5.0% (3 peers), S&P 500 (SPY, price only) +10.0%" in line
    assert "-35.0 points" in line and "-15.0 points" in line and "mostly company-specific" in line
    shared = peers.relative_line("ACME", -30.0, [-25.0, -28.0, -20.0], 10.0, 3)
    assert "mostly shared with the peers" in shared
    small = peers.relative_line("ACME", 12.0, [10.0, 9.0, 11.0], 8.0, 3)
    assert "Neither part is as large as 5 points." in small


def test_relative_performance_without_the_index_and_its_abstentions():
    line = peers.relative_line("ACME", 20.0, [0.0, 1.0, 2.0], None, 3)
    assert "no S&P 500 figure was available" in line and "mostly company-specific" in line
    assert peers.relative_line("ACME", None, [1.0] * 3, 1.0, 3).startswith("Not shown:")
    assert peers.relative_line("ACME", 1.0, [1.0, None, None], 1.0, 3).startswith("Not shown:")


def test_growth_is_against_the_peers_combined_revenue():
    # Revenue = cap / P/S: 100 growing 10% and 300 growing 0% combine to 400 over 390.9.
    ps = [_m(200, psTTM=2.0, revenueGrowthTTMYoy=10.0), _m(600, psTTM=2.0, revenueGrowthTTMYoy=0.0),
          _m(100, psTTM=None)]
    line = peers.growth_line("ACME", 5.0, ps, 2)
    assert "+2.3% for the combined revenue of 2 peers" in line
    assert "faster by 2.7 points, so its share of this set's revenue rose" in line
    assert "within this peer set only" in line.lower()
    assert "about unchanged" in peers.growth_line("ACME", 2.8, ps, 2)
    assert "fell" in peers.growth_line("ACME", -3.0, ps, 2)


def test_growth_abstains_on_missing_inputs():
    ps = [_m(100), _m(100, revenueGrowthTTMYoy=-100.0), _m(100, psTTM=0.0)]
    assert peers.growth_line("ACME", None, ps, 1).startswith("Not shown:")
    assert peers.growth_line("ACME", 1.0, ps, 2).startswith("Not shown:")


# ---- masks ----

def _built(bucket, cfg=None):
    cfg = cfg or peers.config_block({"research": {"peers": {"masks": {
        "default": ["pb", "roe"], "financials": ["ev_ebitda", "revenue_growth", "roi"]}}}})
    raw = {"target": _m(10_000), "candidates": [(t, _m(c)) for t, c in
                                                [("AAA", 9_000), ("BBB", 8_000), ("CCC", 7_000)]],
           "grouping": peers.SUB_INDUSTRY, "spy": 10.0}
    return peers.build("ACME", bucket, raw, cfg, 300.0, "t")


def test_masks_hide_measures_per_bucket_and_bring_in_book_value_and_roe():
    plain = [r.label for r in _built(None).rows]
    assert "P/B" not in plain and "ROE (TTM)" not in plain and "EV/EBITDA (TTM)" in plain
    assert _built("unknown").masked == []
    bank = _built("financials")
    labels = [r.label for r in bank.rows]
    assert "P/B" in labels and "ROE (TTM)" in labels and "EV/EBITDA (TTM)" not in labels
    assert bank.masked == ["EV/EBITDA (TTM)", "Revenue growth (TTM, y/y)", "ROI (TTM)"]
    assert bank.growth == "Not shown: vendor revenue growth is not meaningful for this sector."


def test_the_shipped_masks_cover_the_three_buckets():
    from pathlib import Path

    from shortlist.config import load_config
    cfg = peers.config_block(load_config(str(Path(__file__).resolve().parents[2] / "config.yaml")))
    assert set(cfg["masks"]) == {"default", "financials", "insurer", "reit"}
    known = {c.key for c in peers.COLUMNS}
    assert all(set(v) <= known for v in cfg["masks"].values())
    bank = peers.hidden_columns(cfg, "financials")
    assert {"revenue_growth", "gross_margin", "ev_ebitda", "debt_to_equity"} <= bank
    assert {"roi"} <= peers.hidden_columns(cfg, "reit") and "pb" not in peers.hidden_columns(cfg, "reit")


# ---- fetch ----

def test_a_full_fetch_with_the_key_in_a_header():
    v = _Vendor({"default": ["AAA", "ACME", "BBB", "CCC", "DDD", "DDD"]}, _five())
    t = _fetch(v)
    assert t.status == peers.SHOWN and t.as_of == "2026-11-02 14:00 UTC"
    assert t.peers == ["AAA", "BBB", "CCC", "DDD"]
    assert t.basis.startswith(f"Peer source: {peers.SUB_INDUSTRY}. Of 4 examined,")
    assert all(r.headers["x-finnhub-token"] == "k-123" and "k-123" not in str(r.url)
               for r in v.requests)
    assert v.paths() == [("metric", "ACME", None), ("peers", "ACME", None),
                         ("metric", "AAA", None), ("metric", "BBB", None),
                         ("metric", "CCC", None), ("metric", "DDD", None), ("metric", "SPY", None)]
    assert "S&P 500 (SPY, price only) +11.0%" in t.relative


def test_too_few_usable_peers_retries_once_with_the_industry_list():
    metrics = {**_five(), "TINY": _m(1), "EEE": _m(4_000)}
    v = _Vendor({"default": ["AAA", "TINY", "GONE"], "industry": ["AAA", "BBB", "EEE"]}, metrics)
    t = _fetch(v)
    assert t.status == peers.SHOWN and t.peers == ["AAA", "BBB", "EEE"]
    assert f"Peer source: {peers.WIDER_TOO_FEW}." in t.basis and "1 below $300M" in t.basis
    assert "1 without vendor metrics" in t.basis
    assert [p for p in v.paths() if p[0] == "peers"] == [("peers", "ACME", None),
                                                         ("peers", "ACME", "industry")]
    assert sum(1 for p in v.paths() if p[1] == "AAA") == 1


def test_still_too_few_is_one_not_shown_sentence():
    v = _Vendor({"default": ["AAA"], "industry": ["AAA", "TINY"]},
                {"ACME": _m(10_000), "AAA": _m(900), "TINY": _m(1)})
    t = _fetch(v)
    assert (t.status, t.rows) == (peers.NOT_SHOWN, [])
    assert t.text == peers.TOO_FEW_TEXT.format(n=3, floor="$300M")


def test_the_candidate_budget_bounds_the_calls():
    many = {f"P{i}": _m(1_000 + i) for i in range(30)}
    v = _Vendor({"default": list(many)}, {"ACME": _m(5_000), **many})
    cfg = {"research": {"peers": {**CFG["research"]["peers"], "max_candidates": 4}}}
    t = _fetch(v, cfg=cfg)
    assert sum(1 for p in v.paths() if p[0] == "metric" and p[1].startswith("P")) == 4
    assert len(t.peers) == 4


def _big_target(sub, industry, extra=None):
    """A $100B company: `sub` and `industry` are (ticker, cap in $M) lists."""
    metrics = {"ACME": _m(100_000), **{t: _m(c) for t, c in sub + industry}, **(extra or {})}
    return _Vendor({"default": [t for t, _ in sub], "industry": [t for t, _ in industry]}, metrics)


def test_much_smaller_sub_industry_peers_add_the_industry_list():
    # DE's case: every sub-industry peer is a small fraction of the company.
    v = _big_target([("S1", 5_000), ("S2", 3_000), ("S3", 1_000)],
                    [("B1", 80_000), ("S1", 5_000), ("B2", 150_000), ("B3", 40_000)])
    t = _fetch(v)
    assert t.status == peers.SHOWN
    assert f"Peer source: {peers.WIDER_SMALLER}." in t.basis and "Of 6 examined" in t.basis
    assert t.peers == ["B2", "B1", "B3", "S1", "S2", "S3"]
    assert [p for p in v.paths() if p[0] == "peers"] == [("peers", "ACME", None),
                                                         ("peers", "ACME", "industry")]
    assert sum(1 for p in v.paths() if p[1] == "S1") == 1


def test_the_merged_pool_is_reselected_by_size():
    v = _big_target([(f"S{i}", 1_000 + i) for i in range(6)],
                    [(f"B{i}", 61_000 + 10_000 * i) for i in range(6)])
    t = _fetch(v)
    assert sorted(t.peers) == [f"B{i}" for i in range(6)]


def test_comparable_sub_industry_peers_keep_the_default_list():
    # AMAT's case: the sub-industry holds the real competitors; the industry list is worse.
    v = _big_target([("LRCX", 120_000), ("KLAC", 90_000), ("TER", 20_000), ("ENTG", 15_000)],
                    [("NVDA", 4_000_000), ("AVGO", 1_500_000)])
    t = _fetch(v)
    assert t.basis.startswith(f"Peer source: {peers.SUB_INDUSTRY}.")
    assert [p for p in v.paths() if p[0] == "peers"] == [("peers", "ACME", None)]
    assert "NVDA" not in t.peers


def test_the_size_rule_is_strict_and_skips_an_unknown_company_cap():
    kept = [("A", _m(1_000)), ("B", _m(3_000))]
    assert peers._too_small(kept, 20_001, 0.1) and not peers._too_small(kept, 20_000, 0.1)
    assert not peers._too_small(kept, None, 0.1) and not peers._too_small([], 1e9, 0.1)


def test_an_industry_list_with_nothing_new_keeps_the_default_label():
    v = _big_target([("S1", 5_000), ("S2", 3_000), ("S3", 1_000)], [("S1", 5_000)])
    assert _fetch(v).basis.startswith(f"Peer source: {peers.SUB_INDUSTRY}.")


def test_all_calls_stay_within_the_total_cap_and_keep_the_index():
    v = _big_target([(f"S{i}", 1_000 + i) for i in range(12)],
                    [(f"B{i}", 60_000 + i) for i in range(12)])
    cfg = {"research": {"peers": {**CFG["research"]["peers"], "max_calls": 18}}}
    t = _fetch(v, cfg=cfg)
    assert len(v.requests) == 18 and v.paths()[-1] == ("metric", "SPY", None)
    assert t.status == peers.SHOWN and f"{peers.WIDER_SMALLER}" in t.basis
    assert sum(1 for p in v.paths() if p[1].startswith("B")) == 2


def test_the_index_is_fetched_once_a_day():
    v = _Vendor({"default": ["AAA", "BBB", "CCC"]}, _five())
    _fetch(v)
    _fetch(v)
    assert sum(1 for p in v.paths() if p[1] == "SPY") == 1


def test_an_index_failure_drops_only_the_index():
    v = _Vendor({"default": ["AAA", "BBB", "CCC"]}, _five(), spy=None)
    v.metrics["SPY"] = 500
    t = _fetch(v)
    assert t.status == peers.SHOWN and "no S&P 500 figure was available" in t.relative


def test_no_key_is_not_shown_without_a_request(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY")
    v = _Vendor({"default": ["AAA"]}, _five())
    assert _fetch(v).text == peers.NO_KEY_TEXT and v.requests == []


@pytest.mark.parametrize("peer_body", [429, 500, {"error": "x"}, httpx.ConnectError("boom k-123")])
def test_a_peer_list_failure_is_unavailable_and_never_leaks_the_key(peer_body, capsys):
    v = _Vendor({"default": peer_body}, _five())
    t = _fetch(v)
    assert (t.status, t.text, t.rows) == (peers.UNAVAILABLE, peers.UNAVAILABLE_TEXT, [])
    assert "k-123" not in capsys.readouterr().err


def test_a_429_on_any_peer_abandons_the_section_but_another_error_drops_the_peer():
    rate = _Vendor({"default": ["AAA", "BBB", "CCC", "DDD"]}, {**_five(), "BBB": 429})
    assert _fetch(rate).status == peers.UNAVAILABLE
    other = _Vendor({"default": ["AAA", "BBB", "CCC", "DDD"]}, {**_five(), "BBB": 403})
    t = _fetch(other)
    assert t.status == peers.SHOWN and "BBB" not in t.peers


def test_no_company_metrics_and_no_peers_have_their_own_sentences():
    assert _fetch(_Vendor({"default": ["AAA"]}, {"ACME": None})).text == peers.NO_TARGET_TEXT
    assert _fetch(_Vendor({"default": [], "industry": ["ACME"]}, _five())).text == \
        peers.NO_PEERS_TEXT


def test_a_stalled_fetch_is_abandoned_at_the_deadline_and_makes_no_further_call():
    calls = []

    def slow(request):
        calls.append(request)
        time.sleep(0.6)
        return httpx.Response(200, json={"symbol": "ACME", "metric": _m(10_000)})
    cfg = {"research": {"peers": {**CFG["research"]["peers"], "timeout": 0.3}}}
    t0 = time.monotonic()
    t = peers.fetch_section(SimpleNamespace(ticker="ACME"), cfg, now=NOW,
                            transport=httpx.MockTransport(slow))
    assert t.status == peers.UNAVAILABLE and time.monotonic() - t0 < 0.6
    time.sleep(0.8)
    assert len(calls) == 1


def test_the_deadline_is_clamped():
    slow = {"research": {"peers": {"timeout": 900}}}
    assert min(max(float(peers.config_block(slow)["timeout"]), 0.1), peers.MAX_DEADLINE_S) == 45.0


def test_the_pacer_spaces_calls(monkeypatch):
    monkeypatch.setattr(peers, "_last_call", [0.0])
    t0 = time.monotonic()
    for _ in range(3):
        peers._pace(0.1)
    assert time.monotonic() - t0 >= 0.19


def test_an_internal_error_is_unavailable(monkeypatch):
    def boom(*a, **k):
        raise KeyError("x")
    monkeypatch.setattr(peers, "build", boom)
    assert _fetch(_Vendor({"default": ["AAA", "BBB", "CCC"]}, _five())).status == peers.UNAVAILABLE


# ---- rendering and the record ----

def test_markdown_renders_the_table_lines_and_notes():
    t = _built("financials")
    md = "\n".join(peers.markdown_lines(t))
    assert f"## {peers.HEADING}" in md and "| Measure | ACME | Peer median (n) | ACME rank |" in md
    assert "| P/B | 3.0x | 3.0x (3) | lowest of 4 |" in md
    assert "Not meaningful for this sector, so not shown: EV/EBITDA (TTM)" in md
    assert "- **Relative performance:** " in md and "- **Growth vs the peer set:** Not shown" in md
    assert peers.markdown_lines(None) == [] and peers.markdown_lines(PeerTable()) == []


def test_markdown_not_shown_is_one_sentence():
    md = peers.markdown_lines(PeerTable(peers.UNAVAILABLE, peers.UNAVAILABLE_TEXT, "t", "ACME"))
    assert md[-1] == f"_{peers.UNAVAILABLE_TEXT}_" and "|" not in "\n".join(md)


def test_the_record_round_trips_and_junk_degrades():
    import dataclasses
    t = _built(None)
    back = peers.from_record(dataclasses.asdict(t))
    assert (back.rows, back.peers, back.relative, back.values) == (t.rows, t.peers, t.relative, {})
    assert peers.from_record(None) is None and peers.from_record({"status": ""}) is None
    junk = peers.from_record({"status": "shown", "rows": ["x", {"label": 1, "n": "?"}],
                              "peers": ["A", 3]})
    assert junk.rows[0].label == "1" and junk.rows[0].n == 0 and junk.peers == ["A"]


def test_chat_text():
    assert peers.text_lines(None) == []
    lines = peers.text_lines(_built(None))
    assert lines[0].startswith("   🧭 Peer comparison (vendor data, not seen by the model) vs AAA")
    assert peers.text_lines(PeerTable(peers.UNAVAILABLE, peers.UNAVAILABLE_TEXT)) == [
        "   🧭 Peer comparison: not shown: the vendor data was unavailable for this brief (an "
        "error, a rate limit or the time limit). This says nothing about the company's peers."]


@pytest.mark.live
def test_live_nke(monkeypatch):
    from shortlist.env import load_env
    monkeypatch.delenv("FINNHUB_API_KEY")
    load_env()
    t = peers.fetch_section(SimpleNamespace(ticker="NKE", sic_bucket=None),
                            {"research": {"peers": {"enabled": True}}})
    assert t.status == peers.SHOWN and len(t.peers) >= 3
