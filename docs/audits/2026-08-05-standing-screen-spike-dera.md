# Standing-screen spike — SEC DERA, and the decision vs FDIC (2026-08-05)

**Purpose (plan Phase 2):** the like-for-like comparison the FDIC spike
(`2026-08-05-standing-screen-spike-fdic.md` §5) said was the honest next step — measuring the
same three things, so the standing screen is chosen on evidence rather than on which universe
was easiest to win with.

**Verdict: DERA, decisively.** It beats FDIC on every axis that matters except raw in-band
percentage, and the percentage gap is an artefact of FDIC's narrowness.

> **⚠ CORRECTED 2026-08-05 — see `2026-08-05-standing-screen-data-source.md` §5.**
> Two claims below are wrong as stated. (1) **"survivorship-free" applies to the ARCHIVE, not
> to this measurement**: the crosswalk resolved CIK→ticker against *today's*
> `company_tickers.json`, so filers that have since delisted silently fail to resolve — the
> 4,620/88.3% figures are "currently-listed filers". A DERA backfill must use the
> point-in-time `scout/symbology.py:Symbology`, as `backfill.py:784` already does.
> (2) **`filed` alone does not make it point-in-time**: a period reappears as a comparative in
> later quarterly ZIPs and can carry RESTATED values, so facts must be pinned to the filing
> whose own `filed` matches the evaluation point.
> Separately, DERA is **127–215 days stale** and is NOT adopted as a live source.

**Still a spike. NO production wiring.** Scripts: scratchpad `dera_spike.py`,
`dera_num_spike.py`. No Yahoo; caps sampled from Finnhub.

---

## 1. Head-to-head

| | FDIC BankFind | **SEC DERA** |
|---|---|---|
| requests for the whole universe | 1 | **1** (85 MB `2026q1.zip`) |
| peak RSS | 43 MB | **52 MB** |
| distinct listed tickers | 196 | **4,620** |
| crosswalk method | issuer-name match | **CIK, directly in `sub.txt`** |
| crosswalk yield | 6.0% of institutions | **88.3% of periodic filings** |
| point-in-time | needs construction | **native** — `filed` on 100% of 6,169 rows |
| survivorship | n/a | delisted filers remain in historical files |
| sector coverage | banks only | **all** |
| share in $0.3–10B | **59.0%** | 50.3% |
| **in-band names (absolute)** | ~115 | **~2,300** |

FDIC wins the *percentage*; DERA yields **~20× more names actually in the band**. A narrow,
homogeneous universe makes a high in-band share easy — which is exactly why §5 of the FDIC
audit refused to call it on that number alone.

DERA also avoids both FDIC blockers: it does not concentrate the quiet-day digest in one
sector, and it does not sit inside the region where `sectors.masked_legs` strips `moat`
entirely from the scorer.

## 2. Feasibility — the 559 MB question, answered

`sub.txt` (the universe) is only **1.9 MB**. The fundamentals live in `num.txt`, **559 MB
uncompressed**, which was the one real risk on a 1.9 GB box.

Streaming it out of the zip with **stdlib `csv` + `zipfile`**, filtering to six tags:

```
streamed 3,690,955 rows in 11s
filings with BOTH Assets and NetIncomeLoss: 5,433
PEAK RSS: 32 MB   (box total 1,919 MB)
```

**11 seconds, 32 MB.** This corrects the external research's advice ("don't load `num.txt`
into pandas — stream it into DuckDB"): that is right for arbitrary SQL, but our access
pattern is a tag filter over a single pass, so **no new dependency is needed**. Given the
repo's dependency discipline, avoiding DuckDB is worth stating explicitly.

## 3. Why this matters more than "more candidates"

The standing screen's real job is **not** to add rows to the funnel. Deep-screening is
quota-bound at ~10 names/day (`scout.daily_x`), so a 4,620-name universe is useless on its
own — the binding constraint is *which 10 are worth the FMP slots*.

DERA is the only option spiked that answers that, because `num.txt` gives **cheap
fundamentals for the entire universe in one quarterly pass**. That makes a genuine pre-rank
possible *before* any per-ticker quota is spent — which is a different and better thing than
what the Yahoo screener was ever going to provide.

## 4. What is NOT yet established

- **No signal claim whatsoever.** This spike measured feasibility, crosswalk and composition.
  It says nothing about whether a DERA-derived ranking predicts anything. Per the plan, a
  standing screen changes which names surface, so it is a scoring-surface change and needs a
  **committed pre-registration before it can influence the digest** — not a plumbing change.
- **Only 2026q1 was pulled.** Quarter-to-quarter schema stability across the 2009Q2→present
  archive is assumed, not verified.
- **Tag coverage is uneven.** 5,433 of 6,169 filings carry both `Assets` and `NetIncomeLoss`;
  the rest would abstain. Which tags are reliably populated across sectors is unmeasured, and
  is the obvious next thing to check before designing any ranking.
- **`20-F`/`40-F` filers (484 rows) are in the data** but the research layer is 10-K-only
  (foreign issuers already get an ADR-aware skip), so they should be excluded, not screened.

## 5. Recommendation

1. **Adopt DERA as the standing-screen substrate**, FDIC as neither primary nor fallback —
   its narrowness is structural, not fixable.
2. **Next step is a tag-coverage measurement**, not a build: which XBRL tags are populated
   for what fraction of the universe, by sector. That determines what a pre-rank can even be
   computed from, and it is another cheap offline pass over data already on disk.
3. **Then pre-register**, then build. The ranking must not touch the digest before its
   cohort exists.
