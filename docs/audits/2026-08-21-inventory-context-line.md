# 2026-08-21 — inventory context line, pre-clamp stance, Lazy-Prices retirement

Dated evidence for the three changes merged as `bac69aa`. Cite this for *why*; read the
code for behaviour. Design: `docs/PLAN_INVENTORY_DECOMPOSITION.md`.

Trigger: a `/deep LULU` + `/deep HDSN` run on 2026-08-20, and an external second opinion
arguing HDSN's negative FCF was a working-capital artifact rather than deterioration.

---

## 1. Four conclusions reached during this work were WRONG

Recorded first because the pattern matters more than any single number. Each was
confident, each came from real data, and each was caught by an adversarial check or a
guard already committed in this repo — not by care in the moment.

| Claim | Status | What was actually true |
|---|---|---|
| "HDSN's FCF ex-inventory *improved* +26.3M → +32.7M" | **REFUTED** | True only for the inventory-only cut. Including AR+AP: +26.7M → **+16.3M**, a 39% decline. |
| "The brief couldn't see the composition" | **REFUTED** | Its Bear line already said "...negative in FY2025 **on an inventory build**". |
| "No real filing pair can score below 0.7 / the cosine floors near 0.9" | **REFUTED** | NVDA vs Permian Basin Royalty Trust = **0.7216**. A 0.02 margin, not 0.2. |
| "The 1.2M residual is acquired inventory" | **REFUTED** | Sign is backwards. It is the inventory **write-down**. |

A fifth, stated earlier in the same session: "HDSN's conviction Low means thin data."
Measured: `confidence 1.0`, `conviction_capped False` — the model returned LOW itself.

**The design consequence.** The original spec had an advisory flag `fcf_inventory_build`
firing when a negative FCF was "fully explained" by an inventory build. That is the
first refuted claim encoded in a machine-readable field, at scale, on exactly the names
where it is least safe. **The flag was cut.** What shipped reports a level and a trend
and lets the human judge.

---

## 2. HDSN working capital — the measurement that killed the flag

Re-derived from SEC companyfacts, USD millions:

| Basis | FY2024 | FY2025 | change |
|---|---|---|---|
| FCF (OCF + capex) | +86.51 | −8.21 | — |
| FCF ex-**inventory only** | +26.26 | **+32.70** | +24% |
| FCF ex-(inventory + AR + AP) | +26.66 | **+16.30** | **−39%** |
| FCF ex-**all** working capital | +28.77 | **+19.32** | **−33%** |

FY2025 was part-financed by a **+20.2M payables inflow** against −12.7M in FY2024.
Stripping inventory while keeping payables manufactures the improvement.

Multi-year, FCF ex-inventory 2018–2025: −8.09, −10.65, −8.50, +43.73, **+112.23**,
+61.78, +26.26, +32.70. FY2024 is the trough of a four-year ~70% decline.

**What survives:** the swing is dominated by inventory — the inventory line moved
−101.16M against a −94.97M OCF delta, i.e. 107%. And days inventory outstanding, which
does not depend on which lines are stripped:

| | FY2022 | FY2023 | FY2024 | FY2025 |
|---|---|---|---|---|
| DIO (days) | 326.9 | 317.6 | **205.0** | 268.9 |

FY2024's 205 days is the outlier, not FY2025's 269. This is why DIO shipped and the FCF
bridge did not.

## 3. The inventory reconciliation identity

The balance-sheet change, the cash-flow line and the write-down close exactly:

```
FY2025:  BS +39.676 = CF +40.913 − writedown 1.726 + acquired 0.489
FY2024:  BS −58.203 = CF −60.248 − writedown 3.028 + acquired 5.073
```

FY2024's +5.07M of acquired inventory is consistent with the $20.67M USA Refrigerants
deal. Write-downs are **recurring** (2.52 / −2.26 / 3.03 / 1.73M across 2018/23/24/25) —
management has marked this inventory down more than once.

## 4. The two extractors sign the same concept oppositely

| Source | HDSN FY2025 `IncreaseDecreaseInInventories` |
|---|---|
| SEC companyfacts (`_xbrl_facts` path) | **+40,913,000** |
| edgartools DataFrame (`_edgar_facts`, harness) | **−40,913,000** (`preferred_sign: -1.0`) |

Implementing against the wrong one ships a flag firing on inventory *liquidations*.
**Resolved by design, not by testing:** with the FCF bridge cut, nothing needs the
cash-flow line, so only the balance-sheet `InventoryNet` is extracted and the hazard
does not exist in shipped code.

## 5. Lazy Prices — two independent defects

1. **No producer.** Nothing in `src/` writes `StockMetrics.filing_text_similarity`.
   Already known: `TODO.md` §2a, guarded by `tests/test_flag_producers.py` (strict
   xfail). This alone makes the flag unreachable.
2. **The metric barely discriminates.** Measured with the shipped `textsim.py`:

| Comparison | Cosine |
|---|---|
| LULU FY26 vs its own prior year | 0.9966 |
| HDSN FY25 vs its own prior year | 0.9972 |
| HDSN vs LULU (unrelated industries) | 0.8973 |
| **NVDA vs PBT (unrelated industries)** | **0.7216** |

Unrelated companies span 0.72–0.90 against same-firm YoY ~0.997 — the scale barely
separates "changed nothing" from "different company". A realistic same-firm rewrite
cannot reach 0.7: a de-SPAC, where the whole business changes between consecutive 10-Ks
of one CIK, still scores ~0.90–0.92.

**Caution for anyone recalibrating:** the low end is 0.02 above the threshold, and a
truncated extraction (~150 tokens) crosses it — a naive threshold move would fire on
extraction bugs, not rewrites.

---

## 6. Live verification (deployed `bac69aa`, 2026-08-21)

Extraction, against both real 10-Ks:

```
HDSN  inventory ['135.9M', '96.2M']   (companyfacts: 135,923,000 / 96,247,000)  ✓
LULU  inventory ['1,700.8M', '1,442.1M']                                        ✓
```

`/deep HDSN` re-run on the deployed checkout via the bot's path
(`require_passed=False`, 140.3s, $0.5244):

- **No Lazy-Prices section** ✓
- **Gate override named** ✓ — `the tripped negative_fcf gate overrode the model's Hold`,
  plus `Data confidence: 1.00`. The model's HOLD was previously destroyed by
  `apply_guards`; the 2026-08-20 brief could only say "Auto-downgraded".
- **Inventory line reached the prompt** ✓ — balance trend rendered, DIO leg correctly
  absent because FMP 429'd and `gross_profit` is year-joined from FMP (the documented
  limitation, behaving as specified).

**Unplanned observation, n=1 — suggestive, NOT proof.** The old brief's red flag
("operating cash flow swung sharply negative... signaling a genuine cash-burn/liquidity
stress trend") is **absent** from the new one, replaced by the DLA bid-protest contract
rescission (~15% of revenue). The watch item moved from a gross-margin proxy to
"operating cash flow returning positive as the elevated inventory converts to
sales/cash". Consistent with the context line doing its job, but this is one run of a
non-deterministic model and must not be cited as evidence of effect.

## 7. Acceptance criteria NOT yet met

Per the plan's §4, still outstanding — the change ships as descriptive context, which is
why this is a gap to close rather than a blocker:

- the reconciliation identity (§3) closed on n ≥ 10 filers, including ≥3 international
  filers where FX is a reconciling item. Currently n=2 (HDSN, and LULU as an FX example
  at +258.7M balance vs +188.7M cash-flow, a 70.0M gap).
- the rendered line hand-checked against filed statements beyond HDSN and LULU.

## 8. Scope deliberately not taken

- No scoring change, no flag, no gate change. `negative_fcf` still clamps HDSN to Avoid;
  no argument is made that it is wrong, because the evidence does not support one.
- `_xbrl_facts.py` / the backtest untouched — a scope boundary, not a standing exemption.
- Alt-data brand signals (search trends, app ranks, social engagement): no reliable free
  source, and as non-filing text they could not be grounded — a model could assert brand
  erosion with nothing to verify against.
