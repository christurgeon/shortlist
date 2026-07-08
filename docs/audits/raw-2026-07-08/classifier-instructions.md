# 8-K Item 3.03 composition classifier — instructions (v2, FROZEN 2026-07-08 after the one
# allowed calibration revision; revision memo at the bottom cites only ambiguity cases)

You classify SEC Form 8-K filings that report BOTH Item 1.01 (entry into a material
definitive agreement) and Item 3.03 (material modification to rights of security holders).
For each filing you are given: accession number (dashed), CIK (10-digit), filing date.

## Fetching the filing (exact recipe)

1. Index page: `https://www.sec.gov/Archives/edgar/data/<CIK-no-leading-zeros>/<ACC-NO-DASHES>/<ACC-DASHED>-index.htm`
   (example: accession 0001104659-26-076652, CIK 0001456772 →
   `https://www.sec.gov/Archives/edgar/data/1456772/000110465926076652/0001104659-26-076652-index.htm`)
2. In the document table, fetch the row whose **Type is `8-K`** (the primary document).
3. If the Item 3.03 substance is only referenced into an exhibit, you may fetch ONE exhibit.
4. Use header `User-Agent: shortlist-audit turgechr@duck.com`. Fetch SERIALLY (one at a
   time), pause ~1s between requests, back off and retry once on 403/5xx.
5. If the document cannot be fetched or parsed after retries, label `unreadable` and record
   the mechanical evidence (HTTP status / what failed). `unreadable` is ONLY for mechanical
   failure — a readable-but-ambiguous filing is NEVER `unreadable`.

## Primary label — the dominant trigger of Item 3.03, by this precedence when several apply

1. `merger_agreement` — the filing reports entry into (or consummation of) a merger,
   acquisition, business-combination, or tender-support agreement in which **control of a
   whole company changes hands** (either side of the deal). A sale of only a segment,
   subsidiary, or asset package — where the filer itself is not the subject of a control
   transaction — is NOT this label (use `other` with sub_label `asset_sale`).
   **Mandatory sub-tags when this label is chosen:**
   - `de_spac`: `yes` if the transaction is a SPAC business combination (a blank-check /
     acquisition company combining with an operating target), else `no`.
   - `side`: applies to CONVENTIONAL whole-company M&A only — `target` (the filer's common
     holders are bought out / exchanged), `acquirer` (the filer purchases another whole
     company), `unclear` otherwise. **Fixed conventions:** when `de_spac` is `yes`, set
     `side` to `unclear` (the de_spac tag carries the framing; legal-vs-accounting-acquirer
     ambiguity makes side unreliable there). Internalizations, roll-ups, and manager/
     affiliate contributions: `unclear`.
2. `rights_plan` — adoption, amendment, or extension of a stockholder rights plan or an
   NOL-preservation rights plan (Section 382 plan).
3. `reverse_split` — a charter amendment effecting a reverse stock split.
4. `credit_facility` — a credit agreement, loan agreement, notes issuance, or indenture
   whose covenants restrict security-holder rights (for example dividend or repurchase
   restrictions).
5. `other` — none of the above. A `sub_label` is mandatory. **Use these standardized
   sub_labels where they fit, else free text:**
   - `preferred_issuance` — issuance/designation of preferred stock (certificate of
     designations; convertible preferred placements).
   - `ipo_charter` — IPO-related amended-and-restated charter/bylaws (incl. dual-class /
     Up-C structures).
   - `ch11_emergence` — Chapter 11 plan-of-reorganization emergence (old equity/notes
     cancelled, new securities issued to creditors).
   - `asset_sale` — sale of a segment/subsidiary/asset package (see label 1's carve-out
     rule).

Also record: `secondary` — every OTHER label from the list whose subject matter is present
anywhere in the filing (regardless of whether it drove Item 3.03), or []; and one
`evidence` quote of at most 30 words from the filing supporting the primary label.

---
**Revision memo (the one allowed calibration revision, 2026-07-08).** Cases cited (all
definition-ambiguity reports from the out-of-window calibration pass; no label-distribution
information used): 0001104659-26-073803 (segment carve-out vs whole-company M&A → carve-out
rule + `asset_sale`), 0001104659-26-076652 (Chapter 11 emergence had no home →
`ch11_emergence`), 0001104659-26-075150 / 0001829126-26-006250 / 0001193125-26-283064
(de-SPAC side undefined re legal-vs-accounting acquirer / pubco structures → de_spac ⇒
side=unclear convention), 0001193125-26-286851 (internalization → side=unclear),
0001193125-26-270430 / 0001104659-26-078916 / 0001829126-26-006400 / 0001493152-26-031675
(preferred issuances recurrent → standardized `preferred_issuance`), 0001193125-26-269880
(`ipo_charter`), 0001140361-26-025340 (secondary-label rule made explicit). Definitions are
now FROZEN; the in-window sample is classified exactly once under this version.

## Output — strict JSON, one object per filing, no prose

```json
{"adsh": "...", "primary": "...", "de_spac": "yes|no|null", "side": "target|acquirer|unclear|null",
 "secondary": [...], "sub_label": "...|null", "evidence": "...", "unreadable_evidence": null}
```
`de_spac`/`side` are null unless primary is merger_agreement. `sub_label` null unless
primary is other. If unreadable: `"primary": "unreadable"` + the mechanical evidence.
