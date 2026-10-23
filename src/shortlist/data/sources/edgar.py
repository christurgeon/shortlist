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

# SEC allows ~10 req/s per IP. The collector's semaphore cannot see EDGAR calls (they
# run in a worker thread), so all EdgarSource work goes through this gate. It is
# re-created when the event loop changes.
_EDGAR_MAX_CONCURRENCY = 3
_edgar_gate: dict = {}

# Hard cap on in-window Form 4s parsed per ticker; every parse costs SEC requests.
# Past it, only the newest filings count and net_value_6m / counts are understated.
_FORM4_FETCH_LIMIT = 200

# Must be a filer whose companyconcept for DILUTED_SHARES_TAG exists. SEC answers every
# miss (unknown filer, tag or path) with the same 404, so a 404 counts as "tag not
# reported" only while this filer's concept resolves.
_CONCEPT_REFERENCE_CIK = 320193


def _concept_url(cik: int) -> str:
    return (f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}"
            f"/us-gaap/{DILUTED_SHARES_TAG}.json")


def _is_company_not_found(err: Any) -> bool:
    """True when `err` (an exception or an error string) is edgartools' unresolvable-
    ticker failure. Matches the message, not `CompanyNotFoundError`: `edgar` is an
    optional extra and a module-scope import would make the harness require it. A false
    negative only repeats the error and loses no data."""
    return "company not found" in str(err).lower()


def _edgar_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if _edgar_gate.get("loop") is not loop:
        _edgar_gate.update(loop=loop, sem=asyncio.Semaphore(_EDGAR_MAX_CONCURRENCY))
    return _edgar_gate["sem"]


# form prefix (upper) -> Events boolean attribute. Prefix match absorbs /A amendments.
# Both the "SC 13x" and "SCHEDULE 13x" spellings occur.
_EVENT_FORM_PREFIXES = (
    ("SC 13D", "activist_13d"), ("SCHEDULE 13D", "activist_13d"),
    ("SC 13G", "passive_13g"), ("SCHEDULE 13G", "passive_13g"),
    ("8-K", "recent_8k"),
    ("144", "planned_insider_sale_144"),
    # Prefix matching is a trap: a prefix that starts another form silently reclassifies
    # it. A bare "4" would swallow Form 4 and "424B5", so spell entries in full.
    # `tests/test_edgar_events.py` pins the collisions.
    ("NT 10-K", "late_filing"), ("NT 10-Q", "late_filing"),
    ("S-3", "shelf_offering"),        # also S-3ASR / S-3/A / S-3MEF, all shelf capacity
    ("424B5", "shelf_offering"),      # the takedown itself
    ("UPLOAD", "sec_comment_letter"), ("CORRESP", "sec_comment_letter"),
)

# 8-K item codes worth a flag, read from the filings index `items` column at no extra
# request. Narrow on purpose: items 2.02 and 5.02 fire on most names and would be noise.
_EIGHTK_ITEM_FLAGS = (
    ("4.02", "restatement_8k"),
    ("4.01", "auditor_change"),
    ("3.01", "listing_deficiency"),
)

# Form 25 / 25-NSE are excluded on purpose: most are filed for a matured note or warrant,
# not the issuer's common stock, so a delisting flag would often be wrong.


def classify_event_form(form: str) -> Optional[str]:
    """Map a filing form string to its Events flag attribute, or None if not an
    event form. Case-insensitive prefix match (captures /A amendments)."""
    f = (form or "").strip().upper()
    for prefix, attr in _EVENT_FORM_PREFIXES:
        if f.startswith(prefix):
            return attr
    return None


# Only the subject company's row of a 13D/13G/144 carries a file number. A blank one
# means the company filed it about another issuer. CORRESP rows are blank too, so do
# not extend this beyond the three ownership reports.
_SUBJECT_FILED_ATTRS = frozenset({"activist_13d", "passive_13g", "planned_insider_sale_144"})


def is_filed_about_another_issuer(form: str, file_number: Any) -> bool:
    return (classify_event_form(form) in _SUBJECT_FILED_ATTRS
            and not str(file_number or "").strip())


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
    Returns None when there is nothing, never an all-falsy Events (the merge's
    _has_data check relies on it). The latest exact-form 10-Q/10-K filed date is
    carried as `last_report_filed` (the bridge's SUE decay anchor). Exact forms only:
    a 10-Q/A can land months after the print and would freshen the anchor."""
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
    # Memoised `_concept_reference_problem` answer; None means not yet decided.
    _concept_reference_verdict: Optional[str] = None

    def __init__(self, identity: Optional[str] = None, lookback_days: int = 183,
                 config: Optional[dict] = None):
        self.identity = identity or os.environ.get("SEC_IDENTITY")
        if not self.identity:
            raise RuntimeError("SEC_IDENTITY (a contact email) is required by the SEC")
        self.lookback_days = lookback_days
        ev = (config or {}).get("edgar_events", {})
        # 10-Q/10-K feed the SUE decay anchor, never the advisory `recent` list.
        # edgartools includes /A amendments; build_events_section's exact-form compare drops them.
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
            company = Company(ticker)
            # head, not latest: latest returns a bare filing for one row and None for none.
            in_window = company.get_filings(
                form="4", filing_date=f"{cutoff.isoformat()}:",
                sort_by=[("filing_date", "descending")])
            truncated = len(in_window) > _FORM4_FETCH_LIMIT
            summary = aggregate_form4(in_window.head(_FORM4_FETCH_LIMIT), cutoff,
                                      self._conviction, issuer_cik=company.cik)
        except Exception as e:
            res.errors.append(f"edgar: {redact_secrets(e)}")
            res.partial = TickerSnapshot(ticker=ticker)
            return res

        if summary.found:
            res.raw = {"form4_trades": [dataclasses.asdict(t) for t in summary.txns]}
            if truncated:
                res.raw["form4_truncated"] = True
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
        """Map an edgartools Financials onto a Statements-only snapshot. No scaling is
        applied. Pure given `fin`, except that it fires the companyconcept diluted-share
        fallback (a network seam) when the statement view has no share-count row
        (docs/design/PLAN_EDGAR_ROOT_CAUSE_B.md). When `errors` is given, a fallback
        failure is appended to it; a filer that does not report the tag appends nothing."""
        from ...providers._edgar_facts import diluted_shares_from_concept, extract_financials
        try:
            # Units hazard: edgartools returns this scalar in the filer's presentation
            # scale, which can be millions rather than absolute shares
            # (docs/audits/2026-07-31-edgar-concept-match.md). Its only consumer is
            # extract_financials' computed-EPS fallback (`ni / shares_diluted`), so a
            # millions-scaled value gives an EPS 1e6x too small. share_count_cagr and the
            # dilution flag are scale-invariant. No sanity bound is applied: any
            # threshold would be fitted to one observation.
            shares = fin.get_shares_outstanding_diluted()
        except Exception:
            shares = None
        ef = extract_financials(
            fin.income_statement().to_dataframe(),
            fin.cash_flow_statement().to_dataframe(),
            fin.balance_sheet().to_dataframe(),
            shares_diluted=shares,
        )
        if not ef.diluted_shares and ef.fiscal_period_end:
            # Own try/except: a raise here would drop all statements for the ticker,
            # not just diluted_shares (docs/design/PLAN_EDGAR_ROOT_CAUSE_B.md).
            # Best-effort, but record the failure.
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
        # `gross_profit` is not in EdgarFinancials; _merge_statements year-joins it from
        # FMP (docs/design/STATEMENTS_MERGE.md). `total_equity` is extracted directly
        # because that backfill cannot fire on the FMP-gated path. Parent-only concept
        # first, so the value means the same whichever source wins the merge.
        return snap

    def _fetch_sic(self, ticker: str) -> Optional[str]:
        """Network seam (mockable): best-effort SIC off an edgartools Company. Costs
        one extra SEC request per ticker. Returns a 4-digit string or None."""
        from edgar import Company

        from ...sectors import extract_sic
        return extract_sic(Company(ticker))

    def _concept_reference_problem(self) -> str:
        """'' when the reference filer's concept resolves with its tag echoed, else
        what the reference returned. A decisive answer (200 with the tag, a 404, a
        wrong tag, an unreadable body) is memoised per instance; a transient one
        (network error or any other status) is not, so the next 404 asks again."""
        if self._concept_reference_verdict is not None:
            return self._concept_reference_verdict
        import httpx
        try:
            r = httpx.get(_concept_url(_CONCEPT_REFERENCE_CIK),
                          headers={"User-Agent": self.identity}, timeout=10.0)
        except Exception as e:
            return type(e).__name__
        if r.status_code == 404:
            verdict = "404"
        elif r.status_code != 200:
            return str(r.status_code)
        else:
            try:
                tag = r.json().get("tag")
            except Exception:
                verdict = "200 with an unreadable body"
            else:
                verdict = "" if tag == DILUTED_SHARES_TAG else f"200 with tag {str(tag)[:80]!r}"
        self._concept_reference_verdict = verdict
        return verdict

    def _fetch_diluted_shares_concept(
        self, ticker: str, errors: Optional[list[str]] = None,
    ) -> dict:
        """Network seam (mockable): SEC companyconcept for the weighted-average
        diluted share count, fired only when the statement view lacks the row.
        Never raises; returns {} on any failure. A 404 means the filer does not
        report the tag when the reference concept resolves, and is not recorded.
        Any other failure is appended to `errors` when given, so a systemic break
        (blocked UA, changed URL) stays visible.

        Resolves the CIK with edgartools `Company(ticker)`, not the raw
        `company_tickers.json` first-occurrence map, which can pick a shell CIK."""
        import httpx
        from edgar import Company

        try:
            cik = int(Company(ticker).cik)
        except Exception as e:
            if errors is not None:
                errors.append(f"edgar-diluted-shares-concept: {redact_secrets(e)}")
            return {}
        url = _concept_url(cik)
        try:
            r = httpx.get(url, headers={"User-Agent": self.identity}, timeout=10.0)
            if r.status_code == 404:
                problem = self._concept_reference_problem()
                if not problem:
                    return {}
                raise RuntimeError(
                    f"404 for {url}; reference concept "
                    f"CIK{_CONCEPT_REFERENCE_CIK:010d} -> {problem}")
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
        into at most `index_limit` {form, filed, accession, url, items} dicts,
        newest-first. 13D/13G/144 filings the company made about another issuer are
        excluded and do not use up the limit."""
        res = self._raw_filings(ticker)
        if res is None:
            return []
        items = res if hasattr(res, "__iter__") and not hasattr(res, "form") else [res]
        out: list[dict] = []
        for f in items:
            if len(out) >= self._index_limit:
                break
            if is_filed_about_another_issuer(getattr(f, "form", ""),
                                             getattr(f, "file_number", None)):
                continue
            fd = getattr(f, "filing_date", None)
            out.append({
                "form": getattr(f, "form", "") or "",
                "filed": fd.isoformat() if hasattr(fd, "isoformat") else (fd or ""),
                "accession": getattr(f, "accession_no", None),
                "url": getattr(f, "url", None),
                # Without items, an Item 4.02 restatement looks like a routine 8-K.
                "items": getattr(f, "items", None) or None,
            })
        return out

    def _build_events_from_records(self, records: list[dict]) -> Optional[Events]:
        return build_events_section(records, self._event_lookback_days, date.today())

    def _fetch_sync(self, ticker: str) -> SourceResult:
        res = self._fetch_insider(ticker)        # always sets res.partial
        # Ticker resolution is a precondition, not an isolated section: if the symbol is
        # not in SEC's ticker map, no later section can succeed and each would repeat
        # edgartools' nearest-neighbour guess. Report once and skip the rest.
        if res.errors and _is_company_not_found(res.errors[0]):
            res.errors = [
                f"edgar: {ticker} is not in SEC's current ticker map — the issuer may "
                f"have renamed its symbol or been delisted/acquired. No EDGAR data "
                f"(statements, insider, SIC, events) is available under this symbol."]
            return res
        # SIC is isolated. The partial Profile carries only sic; _merge_flat fills the rest.
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
