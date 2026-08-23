from __future__ import annotations

import asyncio
import dataclasses
import os
from datetime import date, timedelta
from typing import Any, Optional

from ...env import redact_secrets
from ...providers._gaap_tags import DILUTED_SHARES_TAG
from ..models import (
    Events,
    FilingEvent,
    Insider,
    InsiderTxn,
    Profile,
    SourceResult,
    Statements,
    TickerSnapshot,
)
from .base import Source

# SEC enforces ~10 req/s fair-access per IP, and each ticker pulls many filings.
# The collector's per-ticker semaphore doesn't bound SEC request *rate* (EDGAR's
# calls happen inside a worker thread, invisible to it), so all EdgarSource work
# is funnelled through this shared gate — well under the limit. Re-created if the
# event loop changes (e.g. a second collect() call) to stay loop-bound-safe.
_EDGAR_MAX_CONCURRENCY = 3
_edgar_gate: dict = {}

# Max recent Form 4 filings fetched per ticker for insider aggregation. A
# high-velocity insider universe could exceed this within the lookback window,
# truncating net_value_6m / buy_count / sell_count (acceptable for typical tickers).
_FORM4_FETCH_LIMIT = 40


def _is_company_not_found(err: Any) -> bool:
    """True when `err` (an exception OR an already-formatted error string) is
    edgartools' unresolvable-ticker failure. Matched on the message rather than by
    importing `CompanyNotFoundError`, because `edgar` is an OPTIONAL extra — importing
    it at module scope would make the whole harness require it. The phrase is stable
    across edgartools releases; a false negative merely restores the old four-error
    behaviour, so this cannot lose data."""
    return "company not found" in str(err).lower()


def _edgar_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if _edgar_gate.get("loop") is not loop:
        _edgar_gate.update(loop=loop, sem=asyncio.Semaphore(_EDGAR_MAX_CONCURRENCY))
    return _edgar_gate["sem"]


# form prefix (upper) -> Events boolean attribute. Prefix match absorbs /A amendments;
# both the "SC 13x" and SEC's "SCHEDULE 13x" spellings are handled (exact-match fetch
# can return either; see spec §3.1).
_EVENT_FORM_PREFIXES = (
    ("SC 13D", "activist_13d"), ("SCHEDULE 13D", "activist_13d"),
    ("SC 13G", "passive_13g"), ("SCHEDULE 13G", "passive_13g"),
    ("8-K", "recent_8k"),
    ("144", "planned_insider_sale_144"),
    # PREFIX MATCHING IS THE TRAP HERE, not a convenience. Every entry below must be
    # checked against the OTHER forms in `edgar_events.forms`: a prefix that is also
    # the start of another form silently reclassifies it. "424B5" is why a bare "4"
    # can never be added (it would swallow Form 4), and it is why these are spelled
    # in full rather than shortened. `tests/test_edgar_events.py` pins the collisions.
    ("NT 10-K", "late_filing"), ("NT 10-Q", "late_filing"),
    ("S-3", "shelf_offering"),        # also S-3ASR / S-3/A / S-3MEF, all shelf capacity
    ("424B5", "shelf_offering"),      # the takedown itself
    ("UPLOAD", "sec_comment_letter"), ("CORRESP", "sec_comment_letter"),
)

# 8-K item codes worth their own flag, read from the `items` column the filings index
# ALREADY carries (so this costs no request). Deliberately narrow: 8-K items 2.02 and
# 5.02 fire on 98% and 89% of names per year and would be noise as flags.
_EIGHTK_ITEM_FLAGS = (
    ("4.02", "restatement_8k"),
    ("4.01", "auditor_change"),
    ("3.01", "listing_deficiency"),
)

# Form 25 / 25-NSE are EXCLUDED on the evidence, not by oversight: 17 of 228 large and
# small/mid caps filed one within a year, essentially all for a matured note or warrant
# rather than the issuer's common stock. As a delisting flag it would be wrong ~7% of
# the time on exactly the names where a delisting flag matters most.


def classify_event_form(form: str) -> Optional[str]:
    """Map a filing form string to its Events flag attribute, or None if not an
    event form. Case-insensitive prefix match (captures /A amendments)."""
    f = (form or "").strip().upper()
    for prefix, attr in _EVENT_FORM_PREFIXES:
        if f.startswith(prefix):
            return attr
    return None


def classify_event_items(items: Any) -> list[str]:
    """Events flag attributes implied by an 8-K's item codes. Accepts the string the
    filings index carries ("4.02,9.01") or a list; unknown codes yield nothing.

    Substring matching would be wrong: "4.02" is a substring of "14.02" and of a bare
    "4.021". Codes are split and compared whole."""
    if not items:
        return []
    raw = items if isinstance(items, (list, tuple)) else str(items).split(",")
    codes = {str(c).strip() for c in raw}
    return [attr for code, attr in _EIGHTK_ITEM_FLAGS if code in codes]


def build_events_section(records: list[dict], lookback_days: int,
                         today: date) -> Optional[Events]:
    """Pure: filter records to the lookback window, classify, and build an Events.
    Returns None when there is nothing at all — NEVER an all-falsy Events
    (load-bearing for the merge's _has_data check; spec §4). Separately from the
    advisory flags, the latest exact-form 10-Q/10-K filed date is carried as
    `last_report_filed` (the bridge's SUE decay anchor) — exact forms only, since
    a 10-Q/A can land months after the print and would wrongly freshen the anchor."""
    cutoff = today - timedelta(days=lookback_days)
    kept: list[tuple[str, FilingEvent]] = []
    item_attrs: set[str] = set()
    report_filed: Optional[str] = None
    for r in records:
        form = r.get("form", "")
        filed = r.get("filed")
        try:
            in_window = date.fromisoformat(filed) >= cutoff
        except (TypeError, ValueError):
            continue
        if form.strip().upper() in ("10-Q", "10-K"):
            if report_filed is None or filed > report_filed:
                report_filed = filed
            continue
        attr = classify_event_form(form)
        if attr is None or not in_window:
            continue
        items = r.get("items") or None
        if attr == "recent_8k":
            item_attrs.update(classify_event_items(items))
        kept.append((attr, FilingEvent(
            form=form, filed=filed,
            accession=r.get("accession"), url=r.get("url"),
            items=items)))
    if not kept and report_filed is None:
        return None
    kept.sort(key=lambda p: p[1].filed, reverse=True)   # newest-first
    ev = Events(recent=[fe for _, fe in kept], last_report_filed=report_filed)
    for attr, _ in kept:
        setattr(ev, attr, True)
    for attr in item_attrs:
        setattr(ev, attr, True)
    return ev


class EdgarSource(Source):
    """Authoritative SEC Form 4 insider data plus annual financials. Free, but the
    blocking `edgartools` work runs in a worker thread (the harness is async) and
    is rate-limited via a shared semaphore. `sentiment_mspr` is Finnhub's signal
    and is composed in by the custom insider merger. Financials failures are
    isolated — they never drop a successfully fetched insider result."""

    name = "edgar"

    def __init__(self, identity: Optional[str] = None, lookback_days: int = 183,
                 config: Optional[dict] = None):
        self.identity = identity or os.environ.get("SEC_IDENTITY")
        if not self.identity:
            raise RuntimeError("SEC_IDENTITY (a contact email) is required by the SEC")
        self.lookback_days = lookback_days
        ev = (config or {}).get("edgar_events", {})
        # 10-Q/10-K are fetched for the SUE decay anchor — they never enter the
        # advisory `recent` list. edgartools auto-includes /A amendments in the
        # fetch; build_events_section's exact-form compare keeps them off the anchor.
        self._event_forms = ev.get(
            "forms", ["8-K", "SC 13D", "SC 13G", "144", "SCHEDULE 13D", "SCHEDULE 13G",
                      "10-Q", "10-K"])
        self._event_lookback_days = ev.get("lookback_days", 90)
        self._index_limit = ev.get("index_limit", 40)
        self._conviction = ((config or {}).get("insider") or {}).get("conviction")
        from edgar import set_identity  # lazy: edgartools is an optional dep
        set_identity(self.identity)  # process-global mutable state — set once, here

    async def fetch(self, ticker: str) -> SourceResult:
        async with _edgar_semaphore():
            return await asyncio.to_thread(self._fetch_sync, ticker)

    def _fetch_insider(self, ticker: str) -> SourceResult:
        """Fetch Form 4 insider data. Always returns a SourceResult with a
        non-None res.partial (on all branches, including the except branch)."""
        from edgar import Company

        from ...providers._form4 import aggregate_form4

        res = SourceResult(source=self.name)
        cutoff = date.today() - timedelta(days=self.lookback_days)
        try:
            summary = aggregate_form4(
                Company(ticker).get_filings(form="4").latest(_FORM4_FETCH_LIMIT), cutoff, self._conviction)
        except Exception as e:
            res.errors.append(f"edgar: {redact_secrets(e)}")
            res.partial = TickerSnapshot(ticker=ticker)
            return res

        if summary.found:
            res.raw = {"form4_trades": [dataclasses.asdict(t) for t in summary.txns]}
            ins = Insider(
                net_value_6m=summary.net_value,
                buy_count=summary.buy_count,
                sell_count=summary.sell_count,
                recent=[InsiderTxn(
                    date=t.date, name=t.name, role=t.role, kind=t.kind,
                    shares=t.shares, price=t.price, value=t.value,
                ) for t in summary.txns[:10]],
            )
            if self._conviction is not None:
                ins.distinct_buyers = summary.distinct_buyers
                ins.role_weighted_buy_value = summary.role_weighted_buy_value
                ins.planned_sell_value = summary.planned_sell_value
            res.partial = TickerSnapshot(ticker=ticker, insider=ins)
        else:
            res.partial = TickerSnapshot(ticker=ticker)
        return res

    def _fetch_financials_object(self, ticker: str) -> Any:
        """Seam for mocking: returns an edgartools Financials (or raises)."""
        from edgar import Company
        return Company(ticker).get_financials()

    def _build_financials_snapshot(
        self, ticker: str, fin: Any, errors: Optional[list[str]] = None,
    ) -> TickerSnapshot:
        """Map an edgartools Financials onto a Statements-only snapshot. Values are
        absolute USD (no scaling). Mostly pure given `fin`, but ALSO fires the
        companyconcept diluted-share fallback (a network seam) when the statement
        view has no share-count row at all -- root cause B
        (docs/PLAN_EDGAR_ROOT_CAUSE_B.md). `errors` is optional (callers that don't
        pass one get the pre-existing silent-degrade behavior); `_fetch_sync` passes
        `res.errors` so a systemic fallback failure (SEC renames the tag, blocks the
        UA, changes the URL) is diagnosable instead of degrading forever with no
        trace, matching the `_fetch_sic` pattern below."""
        from ...providers._edgar_facts import diluted_shares_from_concept, extract_financials
        try:
            # UNITS HAZARD, not fixed here: edgartools returns this scalar in whatever
            # scale the filer's income statement uses. MCD's is ~716.4 -- MILLIONS, not
            # absolute shares (TODO.md §4). Filer-presentation scaling is NOT unique to
            # this scalar: MCD's per-row `ef.diluted_shares` series is the same
            # convention ([716.4, 721.9, 732.3] -- do not mistake that list for this
            # scalar; docs/audits/2026-07-31-edgar-concept-match.md:313).
            # It reaches exactly one consumer: extract_financials' computed-EPS fallback
            # (`ni / shares_diluted`), which fires only when no as-reported EPS row was
            # matched -- so a millions-scaled value there yields an EPS 1e6x too small
            # (MCD's recorded 11,952,819.65 is that bug). share_count_cagr and the
            # dilution flag are unaffected because they are SCALE-INVARIANT, not because
            # the series is absolute. No sanity bound is applied: every candidate
            # threshold is fitted to this one observation, and this repo does not ship
            # n=1 heuristics.
            shares = fin.get_shares_outstanding_diluted()
        except Exception:
            shares = None
        ef = extract_financials(
            fin.income_statement().to_dataframe(),
            fin.cashflow_statement().to_dataframe(),
            fin.balance_sheet().to_dataframe(),
            shares_diluted=shares,
        )
        if not ef.diluted_shares and ef.fiscal_period_end:
            # Own try/except: never let a RECOVERY path reduce coverage. If this
            # raises, `res.partial.statements` in `_fetch_sync` would otherwise
            # never be assigned and the ticker loses ALL statements, not just
            # diluted_shares (C1, docs/PLAN_EDGAR_ROOT_CAUSE_B.md). Best-effort,
            # but -- unlike a bare `contextlib.suppress` -- record what happened.
            try:
                ef.diluted_shares = diluted_shares_from_concept(
                    self._fetch_diluted_shares_concept(ticker, errors), ef.fiscal_period_end)
            except Exception as e:
                if errors is not None:
                    errors.append(f"edgar-diluted-shares-concept: {redact_secrets(e)}")
        snap = TickerSnapshot(ticker=ticker)
        if ef.fiscal_period_end:
            snap.statements = Statements(
                fiscal_years=[int(d[:4]) for d in ef.fiscal_period_end],
                fiscal_period_end=ef.fiscal_period_end,
                revenue=ef.revenue,
                net_income=ef.net_income,
                operating_cash_flow=ef.operating_cash_flow,
                free_cash_flow=ef.free_cash_flow,
                diluted_eps=ef.diluted_eps,
                diluted_shares=ef.diluted_shares,
                total_debt=ef.total_debt,
                total_equity=ef.total_equity,
                cash_and_equivalents=ef.cash_and_equivalents,
                inventory=ef.inventory,
                operating_income=ef.operating_income,
                dep_amort=ef.dep_amort,
                interest_expense=ef.interest_expense,
                ebitda=ef.ebitda,
                total_assets=ef.total_assets,
                asset_growth=ef.asset_growth,
                accruals=ef.accruals,
                dividends_paid=ef.dividends_paid,
                repurchases=ef.repurchases,
                debt_repayments=ef.debt_repayments,
                debt_issuance=ef.debt_issuance,
            )
        # `gross_profit` isn't in EdgarFinancials; _merge_statements year-joins it back in from
        # FMP when available (docs/STATEMENTS_MERGE.md). `total_equity` USED to be in the same
        # boat — it is now extracted directly (2026-08-10), because that FMP backfill cannot
        # fire on the FMP-gated path, which is exactly where bridge.py needs invested capital
        # for a computed ROIC. Parent-only concept first, so the value means the same thing
        # whichever source wins the merge.
        return snap

    def _fetch_sic(self, ticker: str) -> Optional[str]:
        """Network seam (mockable): best-effort SIC off an edgartools Company.
        EdgarSource has no reusable Company handle in its assembly path, so this is
        one extra lightweight SEC request per ticker, bounded by the module
        concurrency semaphore. Returns a 4-digit string or None."""
        from edgar import Company

        from ...sectors import extract_sic
        return extract_sic(Company(ticker))

    def _fetch_diluted_shares_concept(
        self, ticker: str, errors: Optional[list[str]] = None,
    ) -> dict:
        """Network seam (mockable): SEC companyconcept for the weighted-average
        diluted share count. ~35 KB (vs ~4 MB for companyfacts — measured), fired
        only when the statement view lacks the row. Never RAISES -- {} on any
        error -- but, when `errors` is given, records a diagnostic first (the
        `_fetch_sic` pattern: non-fatal failure isolation must still be visible
        in `res.errors`, not degrade silently forever).

        Resolves the CIK off a fresh edgartools `Company(ticker)` (mirroring
        `_fetch_sic` above), NOT the raw `company_tickers.json` first-occurrence
        map -- that map sends XOM to a 1,061-byte fee-filing shell (CIK 2115436);
        `Company("XOM").cik` correctly resolves the operating company (34088)."""
        import httpx
        from edgar import Company

        try:
            cik = int(Company(ticker).cik)
        except Exception as e:
            if errors is not None:
                errors.append(f"edgar-diluted-shares-concept: {redact_secrets(e)}")
            return {}
        url = (
            f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}"
            f"/us-gaap/{DILUTED_SHARES_TAG}.json"
        )
        try:
            r = httpx.get(url, headers={"User-Agent": self.identity}, timeout=10.0)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if errors is not None:
                errors.append(f"edgar-diluted-shares-concept: {redact_secrets(e)}")
            return {}

    def _raw_filings(self, ticker: str) -> Any:
        """Network seam (mockable): the filtered edgartools filings object."""
        from edgar import Company
        return Company(ticker).get_filings(form=self._event_forms)

    def _fetch_filings_index(self, ticker: str) -> list[dict]:
        """Normalize the edgartools result (None | single EntityFiling | collection)
        into a plain list of {form, filed, accession, url, items} dicts."""
        res = self._raw_filings(ticker)
        if res is None:
            return []
        items = res if hasattr(res, "__iter__") and not hasattr(res, "form") else [res]
        out: list[dict] = []
        for f in list(items)[: self._index_limit]:
            fd = getattr(f, "filing_date", None)
            out.append({
                "form": getattr(f, "form", "") or "",
                "filed": fd.isoformat() if hasattr(fd, "isoformat") else (fd or ""),
                "accession": getattr(f, "accession_no", None),
                "url": getattr(f, "url", None),
                # Already in the edgartools filings index (an `items` column); dropping
                # it made an Item 4.02 non-reliance restatement indistinguishable from a
                # routine 8-K in the brief. Costs no additional request.
                "items": getattr(f, "items", None) or None,
            })
        return out

    def _build_events_from_records(self, records: list[dict]) -> Optional[Events]:
        return build_events_section(records, self._event_lookback_days, date.today())

    def _fetch_sync(self, ticker: str) -> SourceResult:
        res = self._fetch_insider(ticker)        # always sets res.partial (existing branches)
        # Ticker resolution is a PRECONDITION, not a fifth isolated section: if the
        # symbol is not in SEC's ticker map, none of the sections below can succeed
        # and each would append its own copy of edgartools' nearest-neighbour guess
        # (four errors suggesting "MMCP (Mag Mile Capital)" for MMC). Report it once,
        # in terms the user can act on, and skip the rest. Measured 2026-08-15: 8 of
        # 238 tickers in the committed universes no longer resolve — 4 renamed their
        # symbol, the rest stopped filing.
        if res.errors and _is_company_not_found(res.errors[0]):
            res.errors = [
                f"edgar: {ticker} is not in SEC's current ticker map — the issuer may "
                f"have renamed its symbol or been delisted/acquired. No EDGAR data "
                f"(statements, insider, SIC, events) is available under this symbol."]
            return res
        # SIC is isolated: a failure must never drop insider/statements/events. We
        # emit a PARTIAL Profile carrying only sic; _merge_flat fills the rest from
        # FMP/Finnhub, so SIC survives even when those gate the symbol's profile.
        try:
            sic = self._fetch_sic(ticker)
            if sic:
                if res.partial.profile is None:
                    res.partial.profile = Profile(sic=sic)
                else:
                    res.partial.profile.sic = sic
        except Exception as e:
            res.errors.append(f"edgar-sic: {redact_secrets(e)}")
        # Financials are isolated: a failure here must never drop the insider result.
        try:
            fin_snap = self._build_financials_snapshot(
                ticker, self._fetch_financials_object(ticker), res.errors)
            if fin_snap.statements is not None:
                res.partial.statements = fin_snap.statements
        except Exception as e:
            res.errors.append(f"edgar-financials: {redact_secrets(e)}")
        # Events are isolated: a failure here must never drop insider/statements.
        try:
            ev = self._build_events_from_records(self._fetch_filings_index(ticker))
            if ev is not None:
                res.partial.events = ev
        except Exception as e:
            res.errors.append(f"edgar-events: {redact_secrets(e)}")
        return res
