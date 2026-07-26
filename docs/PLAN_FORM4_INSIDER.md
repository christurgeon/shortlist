# Form 4 Opportunistic-Insider Originator — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `edgar_form4`'s bare cluster-count heuristic with a Cohen-Malloy-Pomorski
routine/opportunistic insider filter backed by SEC DERA bulk data, with a dollar floor, role
weighting, 10b5-1 exclusion and full daily coverage.

**Architecture:** Two new pure-ish leaves — `scout/dera.py` (bulk history → per-insider
trade-month index) and `scout/insider.py` (Form 4 XML parsing, classification, strength) —
composed by a rewritten `EdgarForm4Signal`. Live and historical paths produce the **same**
`InsiderTxn` record from **raw** fields, pinned by a cross-path identity test.

**Tech Stack:** Python 3.11+, stdlib only for the leaves (`xml.etree.ElementTree`, `csv`,
`zipfile`, `urllib`), `httpx` for live fetching via existing helpers, pytest, uv.

**Spec:** `docs/FORM4_INSIDER.md` — read it first. This plan implements it and does not
re-argue its decisions.

## Global Constraints

- **Design spec is authoritative:** `docs/FORM4_INSIDER.md`. Deviations need a spec amendment.
- **`scoring.score()` must be untouched.** This is discovery plumbing only.
- **Config invariance:** removing the `scout.form4` block must reproduce pre-feature behaviour
  byte-identically (the convention every block in this repo follows).
- **No network in unit tests.** All fixtures committed under `tests/fixtures/form4/`.
- **CI gate, in this order:** `uv run ruff check src tests` then `uv run pytest -q`. A ruff
  finding is a hard gate, not a nit.
- **SEC fair access:** ≤6 req/s sustained, always send a descriptive User-Agent with contact.
- **Secrets:** any error string that might carry a URL goes through `env.py:redact_secrets()`.
- **Never use `as_of_price` as a size/liquidity proxy** — it is split-adjusted (CLAUDE.md).

## Verified facts (live-probed 2026-07-26 — do NOT "fix" these back)

1. **Complete-submission `.txt` carries the full Form 4 XML and is ~4.6 KB.** Path:
   `https://www.sec.gov/Archives/edgar/data/<cik>/<acc_nodash>/<acc_dashed>.txt`. This means
   **one request per filing** — no `index.json` lookup. (The primary document filename is
   arbitrary, e.g. `tm2510963-2_4seq1.xml`, so fetching the `.xml` directly would cost a
   second request per filing.)
2. **`aff10b5One` appears as BOTH `0`/`1` and `false`/`true`** in real filings — `0` in
   accession `0001104659-25-030072` (2025), `false` in Apple's `0001140361-26-025622` (2026).
   The parser MUST accept both encodings.
3. **`transactionPricePerShare` may contain only a `<footnoteId>` and no `<value>`** (e.g. an
   option exercise). Price must be `None`-able; assuming `<value>` exists will crash or
   fabricate a number.
4. **`reportingOwnerRelationship` flags are present-only-when-true in some filings.** Apple's
   has `<isOfficer>true</isOfficer>` and no `<isDirector>` element at all. Treat a missing
   flag as false, and also parse explicit `false`/`0`.
5. **Scalar values nest inside a `<value>` child**, e.g.
   `<transactionShares><value>6000</value></transactionShares>`.
6. **DERA encodings differ from the XML:** dates are `31-MAR-2025` (DD-MON-YYYY),
   `AFF10B5ONE` is `'0'`/`'1'`, `RPTOWNER_RELATIONSHIP` is a comma-joined string
   (`'Director,Officer,TenPercentOwner'`). This is why the cross-path identity test is not
   trivially true.
7. **DERA URL:** `https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/<YYYYqQ>_form345.zip`,
   ~12.8 MB/quarter. `2026q1` published, `2026q2` not yet as of 2026-07-26.
8. **Cross-path fixture pair (use exactly this):** accession `0001104659-25-030072` — issuer
   OKLO (CIK 1849056), owner CIK 0002021774, relationship `Director`, `P` 6000 sh on
   2025-03-27, filed 2025-03-31, `aff10b5One` `0`. Present in the 2025q1 DERA ZIP **and**
   fetchable as XML.
9. **Joint filings must be ABSTAINED, and they are NOT rare in this population.** A Form 4
   can carry several `<reportingOwner>` blocks, and neither source joins a transaction to a
   particular owner. Measured 2025Q1: **1.72%** of all Form 4s are joint, but **12.05%** of
   those containing an open-market purchase are, and **9.5%** of the v1 population (P buys
   ≥ $100k carrying officer/director) — so ~1 in 10 emissions would otherwise carry a wrong
   `owner_cik` and therefore a wrong CMP tier. `InsiderTxn` carries `joint_filing: bool`;
   `qualifies()` rejects it; the count is surfaced in `available()`, never dropped silently.
   See `docs/FORM4_INSIDER.md` §5.1.
10. **DERA rounds `TRANS_PRICEPERSHARE` to 2dp; the XML carries full precision.** This same
   filing is **`24.5686` in the XML and `24.57` in DERA** (found by the Task 1 implementer,
   confirmed 2026-07-26). Consequences: assert `24.5686` in XML-only tests, and the
   cross-path guard must compare price with a tolerance, never `==`. Do not normalise the
   XML down to 2dp to force agreement — that discards real precision from the live path.
   The rounding is immaterial against a $100,000 floor.

## File Structure

| file | responsibility |
|---|---|
| `src/shortlist/scout/insider.py` (new) | **pure** — `InsiderTxn`, Form 4 XML → records, `classify_tier`, `qualifies`, `strength`, `emissions_from_txns`. No I/O. |
| `src/shortlist/scout/dera.py` (new) | DERA ZIP fetch + cache, TSV → the same `InsiderTxn` records, `build_trade_month_index`. I/O lives here. |
| `src/shortlist/scout/signals.py` (modify) | `EdgarForm4Signal` rewritten to compose the two. |
| `src/shortlist/scout/edgar_index.py` (modify) | add `fetch_form4_submissions`; retire `cluster_buys_from_records`. |
| `src/shortlist/scout/preregister/edgar_form4.yaml` (new) | pre-registered measurement parameters. |
| `config.yaml` (modify) | the `scout.form4` block. |
| `tests/fixtures/form4/` (new) | `okло_form4.xml`, `dera_sample.tsv` — committed, no network. |

---

### Task 1: `InsiderTxn` + Form 4 XML parser

**Files:**
- Create: `src/shortlist/scout/insider.py`
- Create: `tests/fixtures/form4/oklo_0001104659-25-030072.xml`
- Test: `tests/test_scout_insider_parse.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `InsiderTxn` (frozen dataclass) and
  `parse_form4_xml(xml: str) -> list[InsiderTxn]`.

- [ ] **Step 1: Save the fixture**

```bash
mkdir -p tests/fixtures/form4
UA="shortlist-test chris turgechr@duck.com"
curl -s -A "$UA" \
  "https://www.sec.gov/Archives/edgar/data/1849056/000110465925030072/0001104659-25-030072.txt" \
  -o tests/fixtures/form4/oklo_0001104659-25-030072.xml
grep -c "<ownershipDocument>" tests/fixtures/form4/oklo_0001104659-25-030072.xml   # expect 1
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_scout_insider_parse.py
from datetime import date
from pathlib import Path

from shortlist.scout.insider import InsiderTxn, parse_form4_xml

FIX = Path(__file__).parent / "fixtures" / "form4"


def _oklo() -> str:
    return (FIX / "oklo_0001104659-25-030072.xml").read_text(errors="replace")


def test_parses_a_real_open_market_purchase():
    txns = parse_form4_xml(_oklo())
    buys = [t for t in txns if t.code == "P"]
    assert len(buys) == 1
    t = buys[0]
    assert t.owner_cik == "0002021774"
    assert t.ticker == "OKLO"
    assert t.date == date(2025, 3, 27)
    assert t.shares == 6000.0
    assert t.price == 24.5686   # XML full precision; DERA rounds this to 24.57
    assert t.plan_10b5_1 is False          # aff10b5One is "0" here, NOT "false"
    assert "director" in t.roles


def test_missing_price_is_none_not_a_crash():
    """transactionPricePerShare can hold only a <footnoteId> (e.g. option exercises)."""
    xml = """<ownershipDocument>
      <issuer><issuerCik>1</issuerCik><issuerTradingSymbol>ZZZ</issuerTradingSymbol></issuer>
      <reportingOwner><reportingOwnerId><rptOwnerCik>9</rptOwnerCik></reportingOwnerId>
        <reportingOwnerRelationship><isOfficer>true</isOfficer></reportingOwnerRelationship>
      </reportingOwner>
      <nonDerivativeTable><nonDerivativeTransaction>
        <transactionDate><value>2025-01-02</value></transactionDate>
        <transactionCoding><transactionCode>M</transactionCode></transactionCoding>
        <transactionAmounts>
          <transactionShares><value>10</value></transactionShares>
          <transactionPricePerShare><footnoteId id="F1"/></transactionPricePerShare>
        </transactionAmounts>
      </nonDerivativeTransaction></nonDerivativeTable>
    </ownershipDocument>"""
    t = parse_form4_xml(xml)[0]
    assert t.price is None and t.shares == 10.0


def test_aff10b5one_accepts_both_encodings():
    def _mk(flag):
        return f"""<ownershipDocument>
          <issuer><issuerCik>1</issuerCik><issuerTradingSymbol>ZZZ</issuerTradingSymbol></issuer>
          <reportingOwner><reportingOwnerId><rptOwnerCik>9</rptOwnerCik></reportingOwnerId>
            <reportingOwnerRelationship><isDirector>true</isDirector></reportingOwnerRelationship>
          </reportingOwner>
          <aff10b5One>{flag}</aff10b5One>
          <nonDerivativeTable><nonDerivativeTransaction>
            <transactionDate><value>2025-01-02</value></transactionDate>
            <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
            <transactionAmounts>
              <transactionShares><value>10</value></transactionShares>
              <transactionPricePerShare><value>5</value></transactionPricePerShare>
            </transactionAmounts>
          </nonDerivativeTransaction></nonDerivativeTable>
        </ownershipDocument>"""
    assert parse_form4_xml(_mk("1"))[0].plan_10b5_1 is True
    assert parse_form4_xml(_mk("true"))[0].plan_10b5_1 is True
    assert parse_form4_xml(_mk("0"))[0].plan_10b5_1 is False
    assert parse_form4_xml(_mk("false"))[0].plan_10b5_1 is False


def test_absent_relationship_flag_is_false_not_missing():
    """Apple's real filing has <isOfficer> and NO <isDirector> element at all."""
    xml = """<ownershipDocument>
      <issuer><issuerCik>1</issuerCik><issuerTradingSymbol>ZZZ</issuerTradingSymbol></issuer>
      <reportingOwner><reportingOwnerId><rptOwnerCik>9</rptOwnerCik></reportingOwnerId>
        <reportingOwnerRelationship><isOfficer>true</isOfficer>
          <officerTitle>CFO</officerTitle></reportingOwnerRelationship>
      </reportingOwner>
      <nonDerivativeTable><nonDerivativeTransaction>
        <transactionDate><value>2025-01-02</value></transactionDate>
        <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
        <transactionAmounts><transactionShares><value>1</value></transactionShares>
        <transactionPricePerShare><value>1</value></transactionPricePerShare></transactionAmounts>
      </nonDerivativeTransaction></nonDerivativeTable>
    </ownershipDocument>"""
    t = parse_form4_xml(xml)[0]
    assert t.roles == frozenset({"officer"})
    assert t.title == "CFO"


def test_malformed_xml_abstains_rather_than_raising():
    assert parse_form4_xml("<not-xml") == []
    assert parse_form4_xml("") == []
```

- [ ] **Step 3: Run the tests, verify they fail**

Run: `uv run pytest tests/test_scout_insider_parse.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'shortlist.scout.insider'`

- [ ] **Step 4: Implement `scout/insider.py`**

```python
"""Pure Form 4 insider math: parse, classify (Cohen-Malloy-Pomorski), score strength.

NO I/O. The bulk-history side lives in scout/dera.py; both produce the SAME InsiderTxn from
RAW fields (never edgartools' normalized view -- that layer drifted between versions and
silently broke the accruals leg; see docs/audits/2026-07-12-accruals-leg-disable.md).

Design: docs/FORM4_INSIDER.md
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date

_TRUE = {"1", "true", "yes", "y"}


@dataclass(frozen=True)
class InsiderTxn:
    owner_cik: str
    ticker: str
    date: date
    code: str
    shares: float | None
    price: float | None
    plan_10b5_1: bool
    roles: frozenset[str]
    title: str | None = None
    # Appended LAST (positional back-compat). True when the filing has >1 reporting owner:
    # neither source joins a transaction to a PARTICULAR owner, so any single attribution is
    # a guess. qualifies() rejects these. 9.5% of the v1 population -- spec §5.1.
    joint_filing: bool = False

    @property
    def value(self) -> float | None:
        if self.shares is None or self.price is None:
            return None
        return self.shares * self.price


def _flag(raw) -> bool:
    """aff10b5One / isOfficer appear as BOTH 0|1 AND false|true in real filings
    (live-verified 2026-07-26). A missing element is False."""
    return str(raw or "").strip().lower() in _TRUE


def _val(node, path: str) -> str | None:
    """Scalar values nest in a <value> child; that child may be absent (a
    <footnoteId>-only price). Returns None rather than fabricating."""
    el = node.find(path)
    if el is None:
        return None
    v = el.find("value")
    text = (v.text if v is not None else el.text) or ""
    text = text.strip()
    return text or None


def _num(node, path: str) -> float | None:
    raw = _val(node, path)
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


def parse_form4_xml(xml: str) -> list[InsiderTxn]:
    """Raw Form 4 XML -> non-derivative transactions. Never raises: malformed input -> []."""
    if not xml:
        return []
    start = xml.find("<ownershipDocument")
    if start >= 0:
        end = xml.find("</ownershipDocument>")
        xml = xml[start:end + len("</ownershipDocument>")] if end > start else xml[start:]
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []

    ticker = (_val(root, "issuer/issuerTradingSymbol") or "").upper()
    owners = root.findall("reportingOwner")
    joint = len(owners) > 1
    owner_cik = _val(root, "reportingOwner/reportingOwnerId/rptOwnerCik") or ""
    rel = root.find("reportingOwner/reportingOwnerRelationship")
    roles = set()
    title = None
    if rel is not None:
        if _flag(getattr(rel.find("isOfficer"), "text", None)):
            roles.add("officer")
        if _flag(getattr(rel.find("isDirector"), "text", None)):
            roles.add("director")
        if _flag(getattr(rel.find("isTenPercentOwner"), "text", None)):
            roles.add("tenpercent")
        t = rel.find("officerTitle")
        title = (t.text or "").strip() or None if t is not None else None
    plan = _flag(getattr(root.find("aff10b5One"), "text", None))

    out: list[InsiderTxn] = []
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        raw_date = _val(tx, "transactionDate")
        if not raw_date:
            continue
        try:
            d = date.fromisoformat(raw_date[:10])
        except ValueError:
            continue
        code_el = tx.find("transactionCoding/transactionCode")
        out.append(InsiderTxn(
            owner_cik=owner_cik, ticker=ticker, date=d,
            code=((code_el.text or "").strip() if code_el is not None else ""),
            shares=_num(tx, "transactionAmounts/transactionShares"),
            price=_num(tx, "transactionAmounts/transactionPricePerShare"),
            plan_10b5_1=plan, roles=frozenset(roles), title=title,
            joint_filing=joint,
        ))
    return out
```

- [ ] **Step 5: Run the tests, verify they pass**

Run: `uv run pytest tests/test_scout_insider_parse.py -q`
Expected: PASS (5 tests)

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src tests
git add src/shortlist/scout/insider.py tests/test_scout_insider_parse.py \
        tests/fixtures/form4/
git commit -m "feat(insider): InsiderTxn + raw Form 4 XML parser

Handles three live-verified landmines: aff10b5One appears as BOTH 0|1 and
false|true; transactionPricePerShare may carry only a footnoteId with no
<value>; relationship flags are present-only-when-true in real filings."
```

---

### Task 2: DERA quarter parser + cross-path identity guard

**Files:**
- Create: `src/shortlist/scout/dera.py`
- Create: `tests/fixtures/form4/dera_2025q1_sample/{SUBMISSION,REPORTINGOWNER,NONDERIV_TRANS}.tsv`
- Test: `tests/test_scout_dera.py`

**Interfaces:**
- Consumes: `InsiderTxn` from Task 1.
- Produces: `parse_dera_tsvs(sub_fh, owner_fh, trans_fh) -> list[InsiderTxn]` and
  `dera_zip_url(quarter: str) -> str`.

- [ ] **Step 1: Build the fixture (three tiny TSVs containing the OKLO filing)**

```bash
mkdir -p tests/fixtures/form4/dera_2025q1_sample
python3 - <<'PY'
import urllib.request, zipfile, io, csv, os
UA = {"User-Agent": "shortlist-test chris turgechr@duck.com"}
url = ("https://www.sec.gov/files/structureddata/data/"
       "insider-transactions-data-sets/2025q1_form345.zip")
raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120).read()
z = zipfile.ZipFile(io.BytesIO(raw))
ACC = "0001104659-25-030072"
out = "tests/fixtures/form4/dera_2025q1_sample"
os.makedirs(out, exist_ok=True)
for name in ("SUBMISSION.tsv", "REPORTINGOWNER.tsv", "NONDERIV_TRANS.tsv"):
    with z.open(name) as fh:
        rdr = csv.reader(io.TextIOWrapper(fh, "utf-8", errors="replace"), delimiter="\t")
        hdr = next(rdr)
        rows = [r for r in rdr if r and r[0] == ACC]
    with open(f"{out}/{name}", "w", newline="") as w:
        cw = csv.writer(w, delimiter="\t")
        cw.writerow(hdr)
        cw.writerows(rows)
    print(name, len(rows), "rows")
PY
```

- [ ] **Step 2: Write the failing test — including the cross-path identity guard**

```python
# tests/test_scout_dera.py
from datetime import date
from pathlib import Path

from shortlist.scout.dera import dera_zip_url, parse_dera_tsvs
from shortlist.scout.insider import parse_form4_xml

FIX = Path(__file__).parent / "fixtures" / "form4"
SAMPLE = FIX / "dera_2025q1_sample"


def _dera():
    with (SAMPLE / "SUBMISSION.tsv").open() as s, \
         (SAMPLE / "REPORTINGOWNER.tsv").open() as o, \
         (SAMPLE / "NONDERIV_TRANS.tsv").open() as t:
        return parse_dera_tsvs(s, o, t)


def test_dera_url_shape():
    assert dera_zip_url("2025q1").endswith(
        "/insider-transactions-data-sets/2025q1_form345.zip")


def test_parses_dera_ddmonyyyy_dates_and_flags():
    buys = [t for t in _dera() if t.code == "P"]
    assert len(buys) == 1
    t = buys[0]
    assert t.date == date(2025, 3, 27)      # from "27-MAR-2025", NOT ISO
    assert t.plan_10b5_1 is False           # AFF10B5ONE is "0"
    assert "director" in t.roles            # from "Director" comma-joined string


def test_live_and_history_agree_on_the_same_filing():
    """THE guard: one real filing, both paths, same record.

    The encodings genuinely differ -- DERA has 27-MAR-2025 / '0' / 'Director', the XML
    has 2025-03-27 / '0' or 'false' / <isDirector> -- so this is not trivially true. It
    is the defence against live-vs-history definitional drift, the failure mode that
    broke the accruals leg.

    Exact equality on the CATEGORICAL fields: those are what drift corrupts (wrong
    column, wrong encoding, wrong sign). PRICE is compared with a tolerance because
    **DERA rounds TRANS_PRICEPERSHARE to 2dp while the XML carries full precision** --
    24.57 vs 24.5686 on this very filing (live-verified 2026-07-26).

    Do NOT "tighten" this to `==`: it will be permanently red. And do NOT normalise the
    XML down to 2dp to make it pass -- that discards real precision from the live path
    to satisfy a test. The rounding is immaterial to a $100k floor.
    """
    xml_t = [t for t in parse_form4_xml(
        (FIX / "oklo_0001104659-25-030072.xml").read_text(errors="replace"))
        if t.code == "P"][0]
    dera_t = [t for t in _dera() if t.code == "P"][0]

    for field in ("owner_cik", "ticker", "date", "code", "roles", "title", "plan_10b5_1"):
        assert getattr(xml_t, field) == getattr(dera_t, field), field
    assert xml_t.shares == dera_t.shares
    assert abs(xml_t.price - dera_t.price) < 0.01          # DERA 2dp rounding only
    assert abs(xml_t.value - dera_t.value) / dera_t.value < 1e-3
```

- [ ] **Step 3: Run the tests, verify they fail**

Run: `uv run pytest tests/test_scout_dera.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'shortlist.scout.dera'`

- [ ] **Step 4: Implement `scout/dera.py`**

```python
"""SEC DERA bulk Form 3/4/5 ingest -> InsiderTxn records + a per-insider trade-month index.

Quarterly ZIPs (~12.8 MB each) at sec.gov/files/structureddata/data/insider-transactions-
data-sets/. Publication lags a quarter, so this is the HISTORY side only; live detection
reads Form 4 XML (scout/insider.py). Both produce the same InsiderTxn from RAW fields.

Design: docs/FORM4_INSIDER.md
"""
from __future__ import annotations

import csv
from datetime import date

from .insider import InsiderTxn

_BASE = ("https://www.sec.gov/files/structureddata/data/"
         "insider-transactions-data-sets")

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}

_TRUE = {"1", "true", "yes", "y"}


def dera_zip_url(quarter: str) -> str:
    """'2025q1' -> the quarterly Form 345 ZIP URL."""
    return f"{_BASE}/{quarter}_form345.zip"


def parse_dera_date(raw: str | None) -> date | None:
    """DERA dates are DD-MON-YYYY ('27-MAR-2025'), NOT ISO. None-safe."""
    s = (raw or "").strip().upper()
    parts = s.split("-")
    if len(parts) != 3 or parts[1] not in _MONTHS:
        return None
    try:
        return date(int(parts[2]), _MONTHS[parts[1]], int(parts[0]))
    except ValueError:
        return None


def _roles(raw: str | None) -> frozenset[str]:
    """'Director,Officer,TenPercentOwner' -> {'director','officer','tenpercent'}."""
    out = set()
    for part in (raw or "").split(","):
        p = part.strip().lower()
        if p == "director":
            out.add("director")
        elif p == "officer":
            out.add("officer")
        elif p in ("tenpercentowner", "tenpercent"):
            out.add("tenpercent")
    return frozenset(out)


def _num(raw: str | None) -> float | None:
    try:
        return float(raw) if (raw or "").strip() else None
    except ValueError:
        return None


def parse_dera_tsvs(sub_fh, owner_fh, trans_fh) -> list[InsiderTxn]:
    """The three DERA TSVs -> InsiderTxn records, matching parse_form4_xml exactly."""
    subs = {r["ACCESSION_NUMBER"]: r for r in csv.DictReader(sub_fh, delimiter="\t")
            if r.get("DOCUMENT_TYPE") == "4"}
    owners: dict[str, list[dict]] = {}
    for r in csv.DictReader(owner_fh, delimiter="\t"):
        owners.setdefault(r["ACCESSION_NUMBER"], []).append(r)

    out: list[InsiderTxn] = []
    for r in csv.DictReader(trans_fh, delimiter="\t"):
        s = subs.get(r["ACCESSION_NUMBER"])
        if not s:
            continue
        d = parse_dera_date(r.get("TRANS_DATE"))
        if d is None:
            continue
        os_ = owners.get(r["ACCESSION_NUMBER"], [])
        o = os_[0] if os_ else {}
        title = (o.get("RPTOWNER_TITLE") or "").strip() or None
        out.append(InsiderTxn(
            owner_cik=(o.get("RPTOWNERCIK") or "").strip(),
            ticker=(s.get("ISSUERTRADINGSYMBOL") or "").strip().upper(),
            date=d,
            code=(r.get("TRANS_CODE") or "").strip(),
            shares=_num(r.get("TRANS_SHARES")),
            price=_num(r.get("TRANS_PRICEPERSHARE")),
            plan_10b5_1=str(s.get("AFF10B5ONE") or "").strip().lower() in _TRUE,
            roles=_roles(o.get("RPTOWNER_RELATIONSHIP")),
            title=title,
            # >1 reporting owner: neither source joins a transaction to a PARTICULAR
            # owner, so any single attribution is a guess. Abstain (spec §5.1).
            joint_filing=len(os_) > 1,
        ))
    return out
```

- [ ] **Step 5: Run the tests, verify they pass**

Run: `uv run pytest tests/test_scout_dera.py -q`
Expected: PASS (3 tests). If `test_live_and_history_parse_to_identical_records` fails,
**fix the parsers, never the assertion** — a mismatch here is the exact drift this guard exists
to catch. Common causes: `owner_cik` zero-padding differs, or `title` is `""` on one side and
`None` on the other.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src tests
git add src/shortlist/scout/dera.py tests/test_scout_dera.py tests/fixtures/form4/
git commit -m "feat(dera): DERA bulk parser + cross-path identity guard

One real filing (OKLO 0001104659-25-030072) parsed via raw Form 4 XML and via
its DERA TSV rows must produce identical InsiderTxn records. The encodings
genuinely differ (27-MAR-2025 vs 2025-03-27, '0' vs 'false', 'Director' vs
<isDirector>), so this is a real guard against live-vs-history drift."
```

---

### Task 3: Trade-month index + CMP routine/opportunistic classification

**Files:**
- Modify: `src/shortlist/scout/dera.py` (add `build_trade_month_index`)
- Modify: `src/shortlist/scout/insider.py` (add `classify_tier`)
- Test: `tests/test_scout_insider_classify.py`

**Interfaces:**
- Consumes: `InsiderTxn` (Task 1), `parse_dera_tsvs` (Task 2).
- Produces: `build_trade_month_index(txns) -> dict[str, set[tuple[int, int]]]` and
  `classify_tier(owner_cik, index, as_of) -> str` returning `"routine" | "opportunistic" |
  "unclassified"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scout_insider_classify.py
from datetime import date

from shortlist.scout.dera import build_trade_month_index
from shortlist.scout.insider import InsiderTxn, classify_tier


def _t(owner, d, code="S"):
    return InsiderTxn(owner_cik=owner, ticker="ZZZ", date=d, code=code,
                      shares=1.0, price=1.0, plan_10b5_1=False,
                      roles=frozenset({"officer"}))


def test_index_uses_all_transaction_codes_not_just_purchases():
    """An insider who SELLS every March is routine -- that is the noise being stripped.
    Indexing only buys would misclassify them as opportunistic."""
    txns = [_t("A", date(y, 3, 10), code="S") for y in (2022, 2023, 2024)]
    idx = build_trade_month_index(txns)
    assert idx["A"] == {(2022, 3), (2023, 3), (2024, 3)}


def test_same_month_three_consecutive_years_is_routine():
    idx = build_trade_month_index([_t("A", date(y, 3, 10)) for y in (2022, 2023, 2024)])
    assert classify_tier("A", idx, as_of=date(2025, 1, 1)) == "routine"


def test_a_gap_year_breaks_the_routine_pattern():
    idx = build_trade_month_index([_t("A", date(y, 3, 10)) for y in (2022, 2024, 2025)])
    assert classify_tier("A", idx, as_of=date(2026, 1, 1)) == "opportunistic"


def test_three_years_of_scattered_months_is_opportunistic():
    idx = build_trade_month_index(
        [_t("A", date(2022, 2, 1)), _t("A", date(2023, 7, 1)), _t("A", date(2024, 11, 1))])
    assert classify_tier("A", idx, as_of=date(2025, 1, 1)) == "opportunistic"


def test_insufficient_history_is_unclassified():
    idx = build_trade_month_index([_t("A", date(2024, 3, 10))])
    assert classify_tier("A", idx, as_of=date(2025, 1, 1)) == "unclassified"
    assert classify_tier("NOBODY", idx, as_of=date(2025, 1, 1)) == "unclassified"


def test_routine_pattern_must_be_within_the_lookback():
    """A same-month streak that ended long ago must not brand a trader routine forever."""
    idx = build_trade_month_index([_t("A", date(y, 3, 10)) for y in (2015, 2016, 2017)])
    assert classify_tier("A", idx, as_of=date(2025, 1, 1)) == "unclassified"
```

- [ ] **Step 2: Run the tests, verify they fail**

Run: `uv run pytest tests/test_scout_insider_classify.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_trade_month_index'`

- [ ] **Step 3: Implement `build_trade_month_index` in `scout/dera.py`**

```python
def build_trade_month_index(txns) -> dict[str, set[tuple[int, int]]]:
    """owner_cik -> {(year, month)} they transacted in.

    Built from ALL transaction codes, deliberately. An insider who sells every March under a
    standing arrangement is ROUTINE -- precisely the noise the CMP filter strips. Indexing
    only purchases would classify such a trader as opportunistic and the filter would do
    nothing. (docs/FORM4_INSIDER.md §6)
    """
    idx: dict[str, set[tuple[int, int]]] = {}
    for t in txns:
        if not t.owner_cik or t.date is None:
            continue
        idx.setdefault(t.owner_cik, set()).add((t.date.year, t.date.month))
    return idx
```

- [ ] **Step 4: Implement `classify_tier` in `scout/insider.py`**

```python
ROUTINE = "routine"
OPPORTUNISTIC = "opportunistic"
UNCLASSIFIED = "unclassified"

_LOOKBACK_YEARS = 3


def classify_tier(owner_cik: str, index: dict, as_of: date,
                  lookback_years: int = _LOOKBACK_YEARS) -> str:
    """Cohen-Malloy-Pomorski (JF 2012) routine/opportunistic split.

    ROUTINE       -- traded in the SAME calendar month in each of the last `lookback_years`
                     consecutive years. Predictable; ~zero abnormal return.
    OPPORTUNISTIC -- has >= lookback_years distinct trading years in the window, but no such
                     month pattern.
    UNCLASSIFIED  -- not enough history to judge. Emitted at reduced strength, never dropped.
    """
    months = index.get(owner_cik)
    if not months:
        return UNCLASSIFIED
    years = [as_of.year - k for k in range(1, lookback_years + 1)]
    in_window = {(y, m) for (y, m) in months if y in years}
    if len({y for (y, _m) in in_window}) < lookback_years:
        return UNCLASSIFIED
    for m in range(1, 13):
        if all((y, m) in in_window for y in years):
            return ROUTINE
    return OPPORTUNISTIC
```

- [ ] **Step 5: Run the tests, verify they pass**

Run: `uv run pytest tests/test_scout_insider_classify.py -q`
Expected: PASS (6 tests)

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src tests
git add src/shortlist/scout/dera.py src/shortlist/scout/insider.py \
        tests/test_scout_insider_classify.py
git commit -m "feat(insider): CMP-2012 routine/opportunistic classification

Trade-month index is built from ALL transaction codes, not just purchases: an
insider who sells every March is routine, and that is exactly the noise being
stripped. The routine streak must fall inside the lookback window so an old
pattern cannot brand a trader forever."
```

---

### Task 4: Qualification, strength and emission assembly

**Files:**
- Modify: `src/shortlist/scout/insider.py`
- Test: `tests/test_scout_insider_emit.py`

**Interfaces:**
- Consumes: `InsiderTxn`, `classify_tier`.
- Produces: `qualifies(txn, tier, cfg) -> bool` and
  `emissions_from_txns(txns, index, as_of, cfg) -> list[Emission]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scout_insider_emit.py
from datetime import date

from shortlist.scout.insider import InsiderTxn, emissions_from_txns

CFG = {"min_value": 100000, "roles": ["officer", "director"],
       "exclude_10b5_1": True,
       "tier_strength": {"opportunistic": 1.0, "unclassified": 0.6}}


def _t(owner, ticker, value, code="P", plan=False, roles=("officer",), title=None):
    return InsiderTxn(owner_cik=owner, ticker=ticker, date=date(2025, 6, 2), code=code,
                      shares=value / 10.0, price=10.0, plan_10b5_1=plan,
                      roles=frozenset(roles), title=title)


def _idx_opportunistic(owner):
    return {owner: {(2024, 1), (2023, 5), (2022, 9)}}


def test_below_the_dollar_floor_does_not_emit():
    ems = emissions_from_txns([_t("A", "ZZZ", 50_000)], _idx_opportunistic("A"),
                              date(2025, 6, 3), CFG)
    assert ems == []


def test_floor_is_per_transaction_never_an_aggregate():
    """Five sub-floor trades must NOT sum past the bar -- that reintroduces the noise
    the floor exists to remove (docs/FORM4_INSIDER.md §7)."""
    txns = [_t(f"A{i}", "ZZZ", 30_000) for i in range(5)]
    idx = {}
    for i in range(5):
        idx.update(_idx_opportunistic(f"A{i}"))
    assert emissions_from_txns(txns, idx, date(2025, 6, 3), CFG) == []


def test_a_qualifying_buy_emits_once_per_issuer():
    txns = [_t("A", "ZZZ", 200_000), _t("B", "ZZZ", 300_000)]
    idx = {**_idx_opportunistic("A"), **_idx_opportunistic("B")}
    ems = emissions_from_txns(txns, idx, date(2025, 6, 3), CFG)
    assert len(ems) == 1 and ems[0].ticker == "ZZZ"


def test_cluster_scores_above_a_lone_buyer_of_the_same_size():
    lone = emissions_from_txns([_t("A", "ZZZ", 200_000)],
                               _idx_opportunistic("A"), date(2025, 6, 3), CFG)
    idx = {**_idx_opportunistic("A"), **_idx_opportunistic("B")}
    clust = emissions_from_txns([_t("A", "ZZZ", 100_000), _t("B", "ZZZ", 100_000)],
                                idx, date(2025, 6, 3), CFG)
    assert clust[0].strength > lone[0].strength


def test_10b5_1_planned_buys_are_excluded():
    ems = emissions_from_txns([_t("A", "ZZZ", 200_000, plan=True)],
                              _idx_opportunistic("A"), date(2025, 6, 3), CFG)
    assert ems == []


def test_non_purchase_codes_are_excluded():
    ems = emissions_from_txns([_t("A", "ZZZ", 200_000, code="S")],
                              _idx_opportunistic("A"), date(2025, 6, 3), CFG)
    assert ems == []


def test_routine_insiders_are_dropped():
    routine = {"A": {(2024, 6), (2023, 6), (2022, 6)}}
    ems = emissions_from_txns([_t("A", "ZZZ", 200_000)], routine, date(2025, 6, 3), CFG)
    assert ems == []


def test_unclassified_emits_at_reduced_strength_and_records_its_tier():
    opp = emissions_from_txns([_t("A", "ZZZ", 200_000)],
                              _idx_opportunistic("A"), date(2025, 6, 3), CFG)
    unc = emissions_from_txns([_t("B", "ZZZ", 200_000)], {}, date(2025, 6, 3), CFG)
    assert unc and unc[0].strength < opp[0].strength
    assert unc[0].meta["tier"] == "unclassified"
    assert opp[0].meta["tier"] == "opportunistic"
```

- [ ] **Step 2: Run the tests, verify they fail**

Run: `uv run pytest tests/test_scout_insider_emit.py -q`
Expected: FAIL — `ImportError: cannot import name 'emissions_from_txns'`

- [ ] **Step 3: Implement in `scout/insider.py`**

```python
from .models import Emission

SIGNAL = "edgar:form4_insider_buy"

# Role weights -- UNFITTED PRIORS. CFO-type titles above CEO-type above other
# (Wang-Shin-Francis 2012 find CFO trades more informative than CEO trades).
_TITLE_WEIGHT = ((("chief financial", "cfo"), 1.00),
                 (("chief executive", "ceo", "president"), 0.90),
                 ((), 0.80))


def _title_weight(title: str | None) -> float:
    t = (title or "").lower()
    for needles, w in _TITLE_WEIGHT:
        if not needles or any(n in t for n in needles):
            return w
    return 0.80


def qualifies(txn: InsiderTxn, tier: str, cfg: dict) -> bool:
    if txn.code != "P" or tier == ROUTINE:
        return False
    # Joint filings carry no per-transaction owner attribution, so owner_cik -- and every
    # tier derived from it -- would be a guess. 9.5% of this population (spec §5.1).
    if txn.joint_filing:
        return False
    if cfg.get("exclude_10b5_1", True) and txn.plan_10b5_1:
        return False
    if not (txn.roles & set(cfg.get("roles") or ("officer", "director"))):
        return False
    v = txn.value
    # PER-TRANSACTION floor, never an aggregate (docs/FORM4_INSIDER.md §7).
    return v is not None and v >= float(cfg.get("min_value", 100_000))


def emissions_from_txns(txns, index: dict, as_of: date, cfg: dict) -> list[Emission]:
    """Qualifying transactions -> one Emission per ISSUER. Pure."""
    by_ticker: dict[str, list[tuple[InsiderTxn, str]]] = {}
    for t in txns:
        tier = classify_tier(t.owner_cik, index, as_of)
        if not t.ticker or not qualifies(t, tier, cfg):
            continue
        by_ticker.setdefault(t.ticker, []).append((t, tier))

    strengths = cfg.get("tier_strength") or {}
    out: list[Emission] = []
    for ticker, rows in by_ticker.items():
        buyers = {t.owner_cik for t, _ in rows}
        total = sum(t.value or 0.0 for t, _ in rows)
        best_tier = OPPORTUNISTIC if any(x == OPPORTUNISTIC for _, x in rows) else UNCLASSIFIED
        tier_mult = float(strengths.get(best_tier, 0.6))
        role_w = max(_title_weight(t.title) for t, _ in rows)
        size = min(0.30, total / 5_000_000.0)          # materiality, capped
        cluster = min(0.20, 0.10 * (len(buyers) - 1))  # cluster is a BONUS, not a gate
        strength = round(min(1.0, (0.50 + size + cluster) * role_w * tier_mult), 4)
        out.append(Emission(
            ticker, SIGNAL, strength,
            f"{len(buyers)} insider buy(s), ${total/1000:.0f}k ({best_tier})",
            is_discovery=True,
            meta={"tier": best_tier, "buyers": len(buyers), "value": total},
        ))
    return out
```

**Note for the implementer:** check `scout/models.py:Emission` for the exact constructor
signature and whether it accepts `meta=`; if it does not, add
`meta: dict = field(default_factory=dict)` as the LAST field (positional back-compat, the
convention this repo uses everywhere).

- [ ] **Step 4: Run the tests, verify they pass**

Run: `uv run pytest tests/test_scout_insider_emit.py -q`
Expected: PASS (8 tests)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src tests
git add src/shortlist/scout/insider.py tests/test_scout_insider_emit.py
git commit -m "feat(insider): qualification, strength and per-issuer emission

Dollar floor is PER-TRANSACTION, never an aggregate. Cluster is a strength
bonus rather than a gate, so a lone material CFO buy still surfaces. Tier is
recorded on every emission so a later cohort can score tiers separately."
```

---

### Task 5: Bulk fetch/cache, live wiring, and full daily coverage

**Files:**
- Modify: `src/shortlist/scout/dera.py` (add `ensure_quarters`, `load_index`)
- Modify: `src/shortlist/scout/edgar_index.py` (add `fetch_form4_submissions`)
- Modify: `src/shortlist/scout/signals.py` (rewrite `EdgarForm4Signal`)
- Modify: `config.yaml`
- Test: `tests/test_scout_form4_signal.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `EdgarForm4Signal` with the existing `scan(session) -> list[Emission]` /
  `available() -> tuple[bool, str]` contract.

- [ ] **Step 1: Write the failing test (no network — inject the fetcher)**

```python
# tests/test_scout_form4_signal.py
from datetime import date
from pathlib import Path

from shortlist.scout.signals import EdgarForm4Signal

FIX = Path(__file__).parent / "fixtures" / "form4"


def test_signal_emits_from_injected_submissions_without_network():
    xml = (FIX / "oklo_0001104659-25-030072.xml").read_text(errors="replace")
    sig = EdgarForm4Signal(
        cfg={"min_value": 100000, "roles": ["officer", "director"],
             "exclude_10b5_1": True,
             "tier_strength": {"opportunistic": 1.0, "unclassified": 0.6}},
        fetch_submissions=lambda session, cap: ([xml], session),
        load_index=lambda: {},          # empty history -> unclassified tier
    )
    ems = sig.scan(date(2025, 3, 31))
    assert [e.ticker for e in ems] == ["OKLO"]
    assert ems[0].meta["tier"] == "unclassified"
    ok, detail = sig.available()
    assert ok and "1" in detail


def test_signal_degrades_quietly_when_the_fetch_fails():
    def boom(session, cap):
        raise RuntimeError("SEC 503")
    sig = EdgarForm4Signal(cfg={}, fetch_submissions=boom, load_index=lambda: {})
    assert sig.scan(date(2025, 3, 31)) == []
    ok, detail = sig.available()
    assert ok is False and "503" in detail
```

- [ ] **Step 2: Run the tests, verify they fail**

Run: `uv run pytest tests/test_scout_form4_signal.py -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'cfg'`

- [ ] **Step 2b: Add the bulk fetch/cache + index loader to `scout/dera.py`**

> **Gap fixed 2026-07-26:** the first draft of this plan referenced `ensure_quarters`,
> `load_index` and `_default_load_index` without ever defining them. Code below.

```python
import json
import urllib.request
import warnings
import zipfile
from pathlib import Path

from ..env import redact_secrets

_UA = "shortlist-scout turgechr@duck.com"


def quarters_back(as_of: date, n: int) -> list[str]:
    """The `n` quarters ending with the one before `as_of`'s, newest first ('2025q1').

    DERA publishes roughly a quarter in arrears, so the CURRENT quarter is normally absent
    and `ensure_quarters` skips it rather than failing (verified 2026-07-26: 2026q1 was
    published, 2026q2 was not).
    """
    y, q = as_of.year, (as_of.month - 1) // 3 + 1
    out = []
    for _ in range(n):
        q -= 1
        if q == 0:
            y, q = y - 1, 4
        out.append(f"{y}q{q}")
    return out


def ensure_quarters(quarters, cache_dir: str, identity: str = _UA) -> list[Path]:
    """Download each quarterly ZIP to `cache_dir` if absent; return the paths that exist.

    Cached FOREVER by filename -- a published quarter is immutable. A 404 means "not
    published yet" and is SKIPPED, never raised: a missing recent quarter must degrade the
    history, not abort the daily run.
    """
    d = Path(cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for q in quarters:
        p = d / f"{q}_form345.zip"
        if not p.exists():
            try:
                req = urllib.request.Request(dera_zip_url(q), headers={"User-Agent": identity})
                with urllib.request.urlopen(req, timeout=120) as r:
                    p.write_bytes(r.read())
            except Exception as exc:  # noqa: BLE001 -- absent quarter degrades history
                warnings.warn(f"dera: {q} unavailable: {redact_secrets(str(exc))}",
                              stacklevel=2)
                continue
        out.append(p)
    return out


def _index_from_zip(path: Path) -> list:
    with zipfile.ZipFile(path) as z:
        with z.open("SUBMISSION.tsv") as s, z.open("REPORTINGOWNER.tsv") as o, \
             z.open("NONDERIV_TRANS.tsv") as t:
            return parse_dera_tsvs(
                io.TextIOWrapper(s, "utf-8", errors="replace"),
                io.TextIOWrapper(o, "utf-8", errors="replace"),
                io.TextIOWrapper(t, "utf-8", errors="replace"))


def load_index(cache_dir: str, quarters, identity: str = _UA) -> dict:
    """Trade-month index across `quarters`, disk-cached as compact JSON.

    Rebuilding from ~16 ZIPs on every daily run is wasteful, so the built index is persisted
    keyed by the exact quarter list. Values are stored as `y*12+m` ints (far smaller than
    [y, m] pairs) and rehydrated to the (year, month) tuples `classify_tier` expects.
    """
    key = "-".join(sorted(quarters))
    cache = Path(cache_dir) / f"index-{key}.json"
    if cache.exists():
        raw = json.loads(cache.read_text())
        return {k: {(v // 12, v % 12 + 1) for v in vs} for k, vs in raw.items()}
    idx: dict[str, set] = {}
    for p in ensure_quarters(quarters, cache_dir, identity):
        for cik, months in build_trade_month_index(_index_from_zip(p)).items():
            idx.setdefault(cik, set()).update(months)
    cache.write_text(json.dumps(
        {k: sorted(y * 12 + (m - 1) for (y, m) in vs) for k, vs in idx.items()}))
    return idx
```

**Memory note — this runs on a 1.9 GB VPS.** Sixteen quarters is roughly 900k submissions.
Build the index **one ZIP at a time** (the loop above does; do not read them all into a list
first) and report the built index's entry count and the process's peak RSS in your task
report. If peak RSS exceeds ~400 MB, stop and report rather than shipping something that
will be OOM-killed alongside the daily scout.

Tests for this step (no network — monkeypatch `ensure_quarters` to return a path to the
committed `dera_2025q1_sample` TSVs zipped into a tmp file, or point `_index_from_zip` at a
tmp ZIP you build in the test):

```python
def test_quarters_back_walks_backwards_from_the_previous_quarter():
    from datetime import date
    from shortlist.scout.dera import quarters_back
    assert quarters_back(date(2026, 7, 26), 3) == ["2026q2", "2026q1", "2025q4"]


def test_load_index_round_trips_through_its_json_cache(tmp_path):
    """Second call must hit the cache and return an identical index."""
    from shortlist.scout.dera import load_index
    # build a one-quarter ZIP from the committed sample TSVs, monkeypatch ensure_quarters
    # to return it, call load_index twice, assert equality and that the cache file exists.
```

- [ ] **Step 3: Add the live fetcher to `scout/edgar_index.py`**

```python
def fetch_form4_submissions(session: date, max_filings: int,
                            identity: str) -> tuple[list[str], date]:
    """Form 4 complete-submission texts for `session` (walk-back to the last published
    index, same rule as fetch_recent_records).

    ONE request per filing: the complete-submission .txt (~4.6 KB) embeds the full Form 4
    XML, so no index.json lookup is needed -- the primary document filename is arbitrary
    (live-verified 2026-07-26). Never raises; returns ([], session) on failure.
    """
    from edgar import get_filings, set_identity
    set_identity(identity)
    filings, used = _walk_back_to_published(
        session, lookback=5,
        fetch=lambda d: list(get_filings(form="4", filing_date=d.isoformat())))
    out: list[str] = []
    for f in _dedup_by_accession(filings)[:max_filings]:
        try:
            out.append(f.text())            # complete submission, XML embedded
        except Exception:                   # noqa: BLE001 -- skip one bad filing
            continue
    return out, used
```

**Implementer note:** confirm `_walk_back_to_published` and `_dedup_by_accession` signatures in
the existing file and adapt; reuse them rather than writing new walk-back logic. If
`f.text()` proves to be more than one request in edgartools, fetch the `.txt` URL directly
with `httpx` at ≤6 req/s instead — the URL shape is in "Verified facts" #1.

- [ ] **Step 4: Rewrite `EdgarForm4Signal` in `scout/signals.py`**

```python
class EdgarForm4Signal:
    """Opportunistic-insider buy discovery from SEC Form 4 (docs/FORM4_INSIDER.md).

    Drift capture, NOT an information edge: Form 4 is public T+2 and every vendor parses it
    instantly. The edge, if any, is selectivity over a months-long post-filing drift.
    """
    name = "edgar_form4"
    is_discovery = True

    def __init__(self, cfg: dict | None = None, max_filings: int = 2000,
                 identity: str | None = None,
                 fetch_submissions=None, load_index=None) -> None:
        self.cfg = cfg or {}
        self.max_filings = max_filings
        self.identity = identity or "shortlist-scout turgechr@duck.com"
        self._fetch = fetch_submissions
        self._load_index = load_index
        self._status = (False, "not run")

    def scan(self, session: date) -> list[Emission]:
        from .insider import emissions_from_txns, parse_form4_xml
        fetch = self._fetch
        if fetch is None:
            from .edgar_index import fetch_form4_submissions as fetch
        try:
            docs, used = fetch(session, self.max_filings)
            index = (self._load_index or self._default_index)()
        except Exception as e:  # noqa: BLE001
            self._status = (False, redact_secrets(str(e)))
            return []
        txns = [t for doc in docs for t in parse_form4_xml(doc)]
        # Spec §5.1: joint filings are abstained, and the count must be VISIBLE -- a silent
        # 9.5% drop is exactly the kind of thing that hides a broken parser.
        n_joint = len({t.ticker for t in txns if t.joint_filing and t.code == "P"})
        ems = emissions_from_txns(txns, index, session, self.cfg)
        cap = int(self.cfg.get("daily_cap", 25))
        if len(ems) > cap:
            ems = sorted(ems, key=lambda e: e.strength, reverse=True)[:cap]
        fallback = "" if used == session else f"; {session} index empty, used {used}"
        joint = f"; {n_joint} joint filings abstained" if n_joint else ""
        self._status = (bool(docs),
                        f"{len(ems)} insider buys from {len(docs)} Form 4s "
                        f"(cap {self.max_filings}){joint}{fallback}")
        return ems

    def available(self) -> tuple[bool, str]:
        return self._status
```

- [ ] **Step 5: Add the config block to `config.yaml`**

```yaml
  form4:                     # EdgarForm4Signal -- opportunistic-insider buys.
                             # DRIFT CAPTURE, not an information edge: Form 4 is public T+2
                             # and every vendor parses it instantly. UNFITTED PRIORS below.
                             # Design: docs/FORM4_INSIDER.md
    min_value: 100000        # PER-TRANSACTION $ floor, never an aggregate (spec §7).
                             # Measured 2025Q1: median buy $23.7k; 31% clear $100k.
    roles: [officer, director]   # tenpercent excluded in v1 (often funds/PE)
    exclude_10b5_1: true
    tier_strength: {opportunistic: 1.0, unclassified: 0.6}   # routine is dropped outright
    daily_cap: 25            # LIVE-ONLY knob -- a backfill cohort must run UNCAPPED
                             # (the 8-K precedent: a live truncation the cohort never applied)
    dera:
      quarters: 16           # ~4y of history, ~205 MB
      cache_dir: .cache/dera
```

- [ ] **Step 6: Run the full suite**

Run: `uv run ruff check src tests && uv run pytest -q`
Expected: ruff clean; all tests pass. `tests/scout/test_daily_demo.py` may fail for an
unrelated, pre-existing, date-dependent reason (it reads live `state/scout_state.json`) —
confirm that failure also occurs on `main` before dismissing it.

- [ ] **Step 7: Commit**

```bash
git add src/shortlist/scout/ config.yaml tests/test_scout_form4_signal.py
git commit -m "feat(scout): wire the opportunistic-insider Form 4 originator

Live path reads the complete-submission .txt (~4.6 KB, XML embedded) at one
request per filing. Raises effective coverage from ~400 to the full daily
Form 4 flow (measured median 838/day, p90 1,498)."
```

---

### Task 6: Pre-registration and docs

> **Scope note (pre-flight, 2026-07-26):** an earlier draft of this task also wired a
> `_BACKFILL_SPECS["form4"]` row. That is **removed** — it contradicted the spec, whose §3
> explicitly defers the backfill cohort from v1. It is also genuinely under-designed: every
> existing leg fetches by date window from an EDGAR/EFTS index, whereas a Form 4 leg walks
> quarterly DERA ZIPs, and its assembler must build the classification index from quarters
> **strictly before** each event's quarter or future trading behaviour leaks into a
> point-in-time classification (a stateful `assemble_factory`, like the `13d-a` row, not the
> pure `assemble` the other three use). That deserves its own spec and plan, not an
> improvised row here. Committing the pre-registration now still preserves the
> anti-p-hacking guarantee: the parameters are fixed and git-timestamped before any run.

**Files:**
- Create: `src/shortlist/scout/preregister/edgar_form4.yaml`
- Modify: `CLAUDE.md`, `docs/FORM4_INSIDER.md` (status), `TODO.md`
- Test: `tests/test_scout_form4_backcompat.py`

**Interfaces:**
- Consumes: `SIGNAL = "edgar:form4_insider_buy"` from Task 4.
- Produces: a committed pre-registration. No backfill wiring.

- [ ] **Step 1: Write the config-invariance test**

```python
# tests/test_scout_form4_backcompat.py
def test_removing_the_form4_block_leaves_the_signal_inert():
    """Convention: deleting a config block reproduces pre-feature behaviour."""
    from datetime import date

    from shortlist.scout.signals import EdgarForm4Signal
    sig = EdgarForm4Signal(cfg={}, fetch_submissions=lambda s, c: ([], s),
                           load_index=lambda: {})
    assert sig.scan(date(2025, 6, 2)) == []
```

- [ ] **Step 2: Run it, verify it passes** (this one guards existing behaviour)

Run: `uv run pytest tests/test_scout_form4_backcompat.py -q`
Expected: PASS

- [ ] **Step 3: Write the pre-registration**

```yaml
# src/shortlist/scout/preregister/edgar_form4.yaml
# Committed BEFORE any backfill/eval run -- the anti-p-hacking guard. The evaluator
# verifies this file's git commit time against the run as_of.
signal: edgar:form4_insider_buy
as_of: 2026-07-26
window_start: 2022-01-01
window_end: 2025-12-31
verdict_as_of: 2026-03-31        # window_end + K; earlier runs are labelled INTERIM
k_months: 3                      # insider drift is measurable at a quarter
factor_model: ff3
weighting: equal
min_measurable_frac: 0.90        # CHECK THIS BEFORE READING ANY ALPHA (audit 2026-07-26 §5)
min_independent_blocks: 8
min_bucket_events: 5
delisting_return: -0.55
regime_down_rule: spy_trailing_3m_negative
expected_sign: positive          # Cohen-Malloy-Pomorski 2012; Lakonishok-Lee 2001
```

- [ ] **Step 4: Update the docs**

- `docs/FORM4_INSIDER.md` — change the status line to `IMPLEMENTED <date>`.
- `CLAUDE.md` — replace the `edgar_form4` description with the new behaviour; add a one-line
  landmine: *"`aff10b5One` appears as BOTH `0|1` and `false|true`; `transactionPricePerShare`
  may carry only a `footnoteId`."*
- `TODO.md` — mark item 2 done; add a follow-up: **the `form4` backfill leg is NOT wired**
  (needs its own spec — quarterly-ZIP fetching plus a point-in-time `assemble_factory`).

- [ ] **Step 5: Full gate and commit**

```bash
uv run ruff check src tests && uv run pytest -q
git add -A
git commit -m "feat(form4): pre-registration and docs

Prereg is committed BEFORE any run. Its min_measurable_frac 0.90 must be
checked BEFORE reading any alpha -- that floor was firing correctly all through
the 2026-07-26 analysis while the levels it rejected were being quoted.

The backfill leg is deliberately NOT wired: spec §3 defers the cohort, and a
Form 4 leg needs quarterly-ZIP fetching plus a point-in-time assemble_factory
(index from quarters strictly BEFORE each event's quarter, or future trading
behaviour leaks into the classification). That needs its own spec."
```

---

## Self-Review

**Spec coverage:** §4 architecture → Tasks 1–5. §5 data contract + identity guard → Task 2.
§6 classification → Task 3. §7 emission/strength → Task 4. §8 config → Task 5 Step 5. §9
measurement → Task 6. §10 testing → distributed across every task. §11 known limits →
documented in code comments and `docs/FORM4_INSIDER.md`, no code required.

**Placeholder scan:** clean after the 2026-07-26 pre-flight pass, which removed two defects —
a stray unused line in Task 4 Step 3, and Task 6's `_BACKFILL_SPECS` row (prose rather than
code, and contradicting spec §3, which defers the cohort). Every remaining code step contains
the code to write. Task 5 Step 3 and Task 4 Step 3 carry implementer notes to verify two
existing signatures (`_walk_back_to_published`, `Emission`) against the codebase rather than
trusting the plan's rendering of them — that is verification, not a placeholder.

**Type consistency:** `InsiderTxn` fields are identical across Tasks 1, 2, 3, 4.
`classify_tier(owner_cik, index, as_of)` is called with that signature in Task 4.
`SIGNAL = "edgar:form4_insider_buy"` matches the prereg `signal:` in Task 6.
`emissions_from_txns(txns, index, as_of, cfg)` is consistent between Tasks 4 and 5.

**Known risk carried into execution:** Task 5's `f.text()` may cost more than one request in
edgartools; the fallback (direct `.txt` fetch) is specified inline with the verified URL shape.
