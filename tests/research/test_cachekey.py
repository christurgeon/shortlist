import pathlib
# tests/research/test_cachekey.py
from datetime import date

from shortlist.research import cachekey


class _M:
    """StockMetrics stub. Only the attributes cachekey reads."""
    def __init__(self, **kw):
        defaults = dict(price=100.0, market_cap=1e11, short_pct_outstanding=None,
                        days_to_cover=None, short_interest_rising=None,
                        revenue_cagr=0.1, roic=0.2, debt_to_equity=0.5,
                        filing_events=None, insider_recent=None, financial_series=None)
        defaults.update(kw)
        for k, v in defaults.items():
            setattr(self, k, v)


class _Card:
    def __init__(self, metrics=None, **kw):
        defaults = dict(quality=50.0, moat=50.0, growth=50.0, momentum=50.0, value=50.0,
                        insider=50.0, risk=50.0, composite=60.0, confidence=0.8,
                        gates=[], flags=[], sic_bucket="unknown")
        defaults.update(kw)
        for k, v in defaults.items():
            setattr(self, k, v)
        if metrics is not None:
            self.metrics = metrics


class _Bundle:
    def __init__(self, cache_key="acc10k+acc10q"):
        self.cache_key = cache_key
        self.primary_accession = "acc10k"


CFG = {"research": {"cache": {"max_age_days": 1, "price_band_pct": 0.03}}}
DAY = date(2026, 8, 12)


def _key(card, cfg=CFG, today=DAY, macro=None):
    return cachekey.brief_key(_Bundle(), card, macro=macro, config=cfg, today=today)


def test_fingerprint_is_8_hex_chars():
    assert len(cachekey.PROMPT_FINGERPRINT) == 8
    int(cachekey.PROMPT_FINGERPRINT, 16)          # raises if not hex


def test_identical_inputs_give_identical_key():
    assert _key(_Card(metrics=_M())) == _key(_Card(metrics=_M()))


def test_key_contains_the_filing_accessions():
    assert _key(_Card(metrics=_M())).startswith("acc10k+acc10q-")


def test_price_move_inside_band_does_not_change_key():
    # VERIFIED arithmetic at band=0.03: _band(100.0) == _band(100.5) == 155,
    # while _band(101.0) == 156. Bucket EDGES exist by construction, so this
    # asserts the property on a pair known to share a bucket - do NOT "fix" a
    # failure here by widening the band.
    assert _key(_Card(metrics=_M(price=100.0))) == _key(_Card(metrics=_M(price=100.5)))


def test_price_move_outside_band_changes_key():
    assert _key(_Card(metrics=_M(price=100.0))) != _key(_Card(metrics=_M(price=140.0)))


def test_new_gate_changes_key():
    assert _key(_Card(metrics=_M())) != _key(_Card(metrics=_M(), gates=["negative_fcf"]))


def test_new_flag_changes_key():
    assert _key(_Card(metrics=_M())) != _key(_Card(metrics=_M(), flags=["cash_burn"]))


def test_new_filing_event_changes_key():
    ev = [{"form": "8-K", "filed": "2026-08-11", "items": "2.02", "accession": "a", "url": None}]
    assert _key(_Card(metrics=_M())) != _key(_Card(metrics=_M(filing_events=ev)))


def test_a_same_day_8k_amendment_does_not_collide_with_its_original():
    """spec §3.7: (form, items, filed) are identical for an 8-K/A filed the same day
    as the report it amends, so the accession has to be in the per-event tuple."""
    orig = [{"form": "8-K", "filed": "2026-08-11", "items": "2.02", "accession": "a1"}]
    amend = [{"form": "8-K", "filed": "2026-08-11", "items": "2.02", "accession": "a2"}]
    assert (_key(_Card(metrics=_M(filing_events=orig)))
            != _key(_Card(metrics=_M(filing_events=amend))))


def test_the_8k_reader_is_in_the_prompt_fingerprint():
    """`eightk` shapes the prompt's bytes, so an edit to it must self-invalidate the
    brief cache — the completeness rule _PROMPT_MODULES exists to enforce."""
    assert "eightk" in cachekey._PROMPT_MODULES


def test_filing_events_with_none_fields_do_not_raise():
    ev = [{"form": "8-K", "filed": None, "items": None},
          {"form": None, "filed": "2026-08-01", "items": "5.02"}]
    assert _key(_Card(metrics=_M(filing_events=ev)))      # sorts without TypeError


def test_extra_insider_trade_changes_key():
    one = [{"date": "2026-08-01", "name": "A", "role": "CEO", "kind": "buy", "value": 500000.0}]
    two = one + [{"date": "2026-08-02", "name": "B", "role": "CFO", "kind": "buy",
                  "value": 500000.0}]
    assert _key(_Card(metrics=_M(insider_recent=one))) != \
        _key(_Card(metrics=_M(insider_recent=two)))


def test_financial_series_change_changes_key():
    a = [{"fiscal_year": 2025, "revenue": 1.00e9, "free_cash_flow": 1.0e8}]
    b = [{"fiscal_year": 2025, "revenue": 1.50e9, "free_cash_flow": 1.0e8}]
    assert _key(_Card(metrics=_M(financial_series=a))) != \
        _key(_Card(metrics=_M(financial_series=b)))


def test_macro_regime_changes_key_and_none_is_safe():
    class _Macro:
        regime = "risk-off"
    assert _key(_Card(metrics=_M()), macro=None) != _key(_Card(metrics=_M()), macro=_Macro())


def test_card_without_metrics_does_not_raise():
    """Mirrors tests/research/test_enrich.py:6-15 — the stub card has no .metrics."""
    assert _key(_Card()) == _key(_Card())


def test_none_price_does_not_raise():
    assert _key(_Card(metrics=_M(price=None, market_cap=None)))


def test_zero_price_does_not_take_log():
    assert _key(_Card(metrics=_M(price=0.0)))     # log(0) would raise


def test_day_rollover_changes_key_when_max_age_is_one():
    c = _Card(metrics=_M())
    assert _key(c, today=date(2026, 8, 12)) != _key(c, today=date(2026, 8, 13))


def test_max_age_zero_disables_the_day_bucket():
    cfg = {"research": {"cache": {"max_age_days": 0}}}
    c = _Card(metrics=_M())
    assert _key(c, cfg=cfg, today=date(2026, 8, 12)) == \
        _key(c, cfg=cfg, today=date(2026, 8, 13))


def test_absent_config_uses_documented_defaults():
    c = _Card(metrics=_M())
    # default max_age_days == 1 -> the day bucket is present, so a rollover changes the key
    assert _key(c, cfg={}, today=date(2026, 8, 12)) != _key(c, cfg={}, today=date(2026, 8, 13))


def test_nan_and_inf_metrics_do_not_raise():
    """math.floor(inf) and round(nan) both raise; _num must reject them before
    they reach the live /deep path."""
    inf, nan = float("inf"), float("nan")
    assert _key(_Card(metrics=_M(price=inf, market_cap=nan, roic=nan)))
    trades = [{"value": nan}, {"value": inf}]
    assert _key(_Card(metrics=_M(insider_recent=trades)))


def test_valuation_field_change_changes_key():
    """pe_ttm renders into the prompt's Valuation line, so it must move the key."""
    assert _key(_Card(metrics=_M(pe_ttm=20.0))) != _key(_Card(metrics=_M(pe_ttm=35.0)))


def test_series_column_beyond_revenue_changes_key():
    """The prompt renders every series column, not just revenue/FCF."""
    a = [{"fiscal_year": 2025, "revenue": 1e9, "diluted_shares": 1.00e8}]
    b = [{"fiscal_year": 2025, "revenue": 1e9, "diluted_shares": 1.50e8}]
    assert _key(_Card(metrics=_M(financial_series=a))) != \
        _key(_Card(metrics=_M(financial_series=b)))


def test_research_config_change_changes_key():
    """max_chars/model/max_risks shape the prompt from YAML, not from source."""
    c = _Card(metrics=_M())
    cfg_a = {"research": {"cache": {"max_age_days": 0}, "max_chars": {"mda": 60000}}}
    cfg_b = {"research": {"cache": {"max_age_days": 0}, "max_chars": {"mda": 10000}}}
    assert _key(c, cfg=cfg_a) != _key(c, cfg=cfg_b)


def test_output_root_change_does_not_change_key():
    """output_root is a path, not prompt content."""
    c = _Card(metrics=_M())
    cfg_a = {"research": {"cache": {"max_age_days": 0}, "output_root": "research"}}
    cfg_b = {"research": {"cache": {"max_age_days": 0}, "output_root": "/tmp/x"}}
    assert _key(c, cfg=cfg_a) == _key(c, cfg=cfg_b)


def test_explicit_zero_price_band_is_honoured():
    """0 is falsy: `_num(v) or DEFAULT` would silently restore 0.03."""
    cfg = {"research": {"cache": {"max_age_days": 0, "price_band_pct": 0}}}
    c1, c2 = _Card(metrics=_M(price=100.0)), _Card(metrics=_M(price=100.5))
    assert _key(c1, cfg=cfg) == _key(c2, cfg=cfg)      # band<=0 -> price drops out


def test_prompt_fingerprint_covers_more_than_assess():
    """The fingerprint must span every prompt-shaping module (SCHEMA_HINT lives
    in models.py; the aux context lines live in their own modules)."""
    assert set(cachekey._PROMPT_MODULES) >= {
        "assess", "models", "reverse_dcf", "coverage_caveat", "proxy",
        "gov_contracts", "lobbying", "earnings", "riskdiff",
        "filings", "textsim"}


# --- I1: insider trades hash direction (kind), not just a gross magnitude sum ---

def test_insider_buy_vs_sell_changes_key():
    """A CEO buy replaced by a CEO sell of the SAME magnitude must NOT read as
    identical: `value` is an unsigned magnitude (providers/_form4.py:113-114),
    direction lives in `kind`, and the old net-sum hash could not see a flip."""
    buy = [{"date": "2026-08-01", "name": "A", "role": "CEO", "kind": "buy",
            "value": 2_000_000.0}]
    sell = [{"date": "2026-08-01", "name": "A", "role": "CEO", "kind": "sell",
             "value": 2_000_000.0}]
    assert _key(_Card(metrics=_M(insider_recent=buy))) != \
        _key(_Card(metrics=_M(insider_recent=sell)))


# --- I2: the DATA GAPS line (coverage status / abstentions) must move the key ---

class _Coverage:
    def __init__(self, providers=None, unavailable=None):
        self.providers = providers or {}
        self.unavailable = unavailable or []


def test_coverage_provider_status_changes_key():
    """Same null axis, different WHY (402 gated vs 429 rate-limited) must not
    read as identical — assess.py renders the reason string into the prompt
    (`_data_gaps_line` / `coverage_caveat._short_reason`)."""
    gated = _Card(metrics=_M(),
                  coverage=_Coverage(providers={"fmp": "gated_402"}, unavailable=["value"]))
    limited = _Card(metrics=_M(),
                    coverage=_Coverage(providers={"fmp": "rate_limited_429"},
                                       unavailable=["value"]))
    assert _key(gated) != _key(limited)


def test_abstentions_change_key():
    a = _Card(metrics=_M(), abstentions=[
        {"field": "value", "scope": "subscore", "reason": "inapplicable"}])
    b = _Card(metrics=_M(), abstentions=[
        {"field": "quality", "scope": "subscore", "reason": "inapplicable"}])
    assert _key(a) != _key(b)


def test_card_without_coverage_or_abstentions_does_not_raise():
    """Mirrors the stub cards in test_enrich.py: no `.coverage`, no `.abstentions`."""
    assert _key(_Card(metrics=_M()))


# --- M2: malformed rows in a .get-looping site must be skipped, not fatal ---

def test_malformed_filing_events_do_not_raise():
    assert _key(_Card(metrics=_M(filing_events=["oops"])))


def test_malformed_insider_trades_do_not_raise():
    assert _key(_Card(metrics=_M(insider_recent=["oops"])))


def test_malformed_financial_series_rows_do_not_raise():
    assert _key(_Card(metrics=_M(financial_series=["oops"])))


def test_fingerprint_fallback_when_source_unavailable(monkeypatch):
    def _boom(_obj):
        raise OSError("source not available")
    monkeypatch.setattr(cachekey.inspect, "getsource", _boom)
    assert cachekey._prompt_fingerprint() == cachekey._FINGERPRINT_FALLBACK


def test_every_context_line_module_is_hashed():
    """The completeness rule, enforced by DISCOVERY rather than by memory.

    `_PROMPT_MODULES` says "ADD TO THIS LIST when a new context-line renderer gets its
    own module", and until this test existed that was enforced only by a hand-written
    assert per module — which is exactly how `options` and `earnings_moves` shipped in
    #194 unhashed. A module whose source is not hashed can have its rendered wording
    changed without invalidating a single cached brief, so a stale brief is served as if
    it were current.
    """
    import pkgutil

    import shortlist.research as research_pkg

    renderers = set()
    for mod in pkgutil.iter_modules(research_pkg.__path__):
        name = mod.name
        if name.startswith("_") or name in ("cachekey", "claude_cli", "phase", "report"):
            continue
        src = (pathlib.Path(research_pkg.__path__[0]) / f"{name}.py")
        if not src.exists():
            continue
        text = src.read_text()
        if "def context_line(" in text or "def format_line(" in text:
            renderers.add(name)

    missing = renderers - set(cachekey._PROMPT_MODULES)
    assert not missing, (
        f"context-line modules missing from _PROMPT_MODULES: {sorted(missing)}. "
        "Their wording could change without busting the brief cache.")
