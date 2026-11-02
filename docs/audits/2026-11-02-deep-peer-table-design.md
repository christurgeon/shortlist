# Peer comparison in `/deep` — design and probe evidence (2026-11-02)

How the section behaves: `docs/RESEARCH.md` → the peer-comparison section. This note keeps the
probe evidence and the decisions, so they are not re-derived. The plan item it implements is
item 1 of `docs/design/DEEP_CONTEXT_PLAN.md`.

## Decisions

- **Display only.** Computed after the model call; not in the prompt, not a grounding segment,
  not a score, gate or flag. Feeding it to the model needs a measured A/B first (the plan's
  sign-off table).
- **One vendor basis: Finnhub** `stock/peers` and `stock/metric?metric=all`. The key is
  already on the VPS. Every number in the table comes from the same vendor field for the
  company and its peers, so a column compares like with like even where the vendor's
  definition differs from ours (the card's own P/E or ROIC can differ from the table's).
- **Peer filter:** drop the company, duplicates, another share class, names with no metrics
  and names below the `gates.min_market_cap` floor; add the `grouping=industry` list once when
  fewer than three remain or when the kept peers' median market cap is below 0.1 of the
  company's; keep up to six closest in log market cap from both lists.
- **Sub-industry stays the default list.** AMAT's sub-industry list (LRCX, KLAC, TER, Q, ENTG)
  holds its competitors; its industry list (NVDA, AVGO, MU, AMD) does not.
- **S&P 500 reference: SPY's own `52WeekPriceReturnDaily`.** `stock/metric?symbol=SPY` returns
  19 fields, including that one. It is a price return on the same field as the stocks. The
  `priceRelativeToS&P500*` fields were not used: their definition is undocumented.
- **Revenue for the growth line: market cap ÷ P/S.** Checked against SEC filings (below).
- **Masks follow the scorer's sector buckets**, with the vendor traps below as the reason.

## Probe evidence (oracle-prod, 2026-11-02)

### Peer list quality (default grouping, the sub-industry)

| Ticker | Finnhub list | Quality |
|---|---|---|
| NKE | DECK, CROX, SHOO, WWW, RCKY, MCRAA, GVSI, BIRD, AURI (GVSI twice) | Footwear only; all much smaller (peer median $3.2B against $57B). GVSI is $0.2M. `grouping=industry` adds RL, TPR, LEVI, VFC, PVH, closer in size. |
| DE | TTC, AGCO, ALG, LNN, TWI, RYM, UAVS, ARTW, HYFM, ICBU, FLZH | Right sub-industry, but six of eleven are below $300M and no peer is near DE's size. CAT and CNH are absent. |
| JPM | BAC, WFC, C, USB, PNC, FITB, FCNCA, KEY, FINN, FRBT | Good. |
| O | SPG, KIM, REG, FRT, BRX, ADC, NNN, MAC, PECO, KRG, SKT | Good: retail REITs. |
| CRWD | MSFT, ORCL, PANW, NOW, FTNT, ZS, RBRK, FFIV, GEN, FROG, NTSK | Mixed: security peers plus MSFT and ORCL. Size selection dropped MSFT. |
| HDSN | NPKI, BXC, TITN, EVI, ALTG, VTMC, MWYN, RGBD, FGI, GOW, TXRP, URI | Poor: a distribution grab-bag; URI is equipment rental. HDSN itself is $181M, below the floor it applies to its peers. |
| TRV | PGR, ALL, AIG, HIG, CINF, WRB, MKL, L, AIZ, CNA, AFG | Good: P&C insurers. |
| GOOGL | META, RDDT, PINS, MTCH, SNAP, CARG, … | GOOG is not listed; share-class dedupe kept as a guard anyway (GOOG and GOOGL carry the same total market cap). |

The list is the vendor's, so its quality decides the section's value. The section names the
peers and how many were dropped, so a reader can discount a poor list.

### Vendor traps

- **JPM "revenue growth" TTM 108.98%**, and +78% median across its bank peers: bank revenue on
  this field includes gross interest income. Masked for `financials` with P/S, margins, ROI,
  EV/EBITDA and debt/equity; P/B and ROE are shown instead.
- **Realty Income P/FCF 1,286** (`pfcfShareTTM`); not a table column. REIT P/E (42.5) and ROI
  (1.84%) are depressed by property depreciation; masked for `reit`.
- **TRV gross margin is null**; insurers mask gross margin, EV/EBITDA, ROI and debt/equity, as
  the scorer does.
- **CRWD P/E 4,386 and EV/EBITDA 2,781**: near-zero earnings, not an error. Shown, ranked; the
  median is robust to it. A negative multiple is shown as n/m and not ranked.
- **DE debt/equity 2.28** includes the captive finance arm; not masked (no bucket for it).
- **Zero debt/equity is a real value** (DECK, HDSN), unlike a zero multiple.
- **An unknown symbol returns HTTP 200 with an empty `metric`** (probed `XXXXZ`); treated as no
  metrics, so the peer is dropped.
- **Units:** percent fields are in percent (`grossMarginTTM` 43.07 = 43.07%), market cap in
  $ millions.

### Checks of the derived inputs

- **Revenue = market cap ÷ P/S.** NKE: 57,436.89 ÷ 1.2516 = 45,891M. From SEC company facts:
  FY ended May 2026 46,398M, minus the August 2025 quarter 11,720M, plus the August 2026
  quarter 11,213M = 45,891M. DECK: 11,165.55 ÷ 2.0021 = 5,577M against a filed TTM of about
  5,577M (FY March 2026 5,472M, rolled forward two quarters). The identity holds to rounding.
- **52-week return is a price return.** Against Yahoo daily closes from 2025-10-31 to
  2026-10-30: SPY 11.16 (vendor) against 10.80 price and 12.00 total return; NKE −40.42
  against −40.13 price and −38.16 total. The gap is the window's end dates.

## The relative-performance rule

The company's 52-week move splits into its gap to the peer median and the peer median's gap to
the S&P 500. The larger part names the move; both under 5 points reads as small. The rule is
deterministic but not neutral: a single stock's gap to a median of several is noisier than the
median's gap to the index, so the rule leans to "company-specific". All six live checks read
"company-specific", and in each the company's gap was at least as large as the other part
(NKE −67 against +16, O −15 against +3). Treat it as a description of the split, not a test.

## The size-mismatch fallback (live, 2026-11-02)

| Ticker | Kept from the sub-industry (median cap) | Fallback | Kept after |
|---|---|---|---|
| DE ($182B) | TTC, AGCO, ALG, LNN, TWI ($1.8B) | yes | CAT, PH, ITW, CMI, PCAR, TTC ($74.0B) |
| NKE ($57B) | DECK, CROX, SHOO, WWW, RCKY ($3.2B) | yes | RL, TPR, DECK, LEVI, CROX, VFC ($9.9B) |
| AMAT ($310B) | LRCX, KLAC, TER, Q, ENTG, MKSI ($33.7B) | no | unchanged |

AMAT sits at a ratio of 0.109, just above the 0.1 threshold. A small move in caps would add the
industry list for it, and the closeness rule would then pull in NVDA-class names. The ratio is
a judgement, not a fit.

## Call budget and timing

Per brief: one call for the company, one or two peer lists, at most `max_candidates` (12)
peer metric calls per list, and one SPY call per UTC day per process, all within `max_calls`
(20). A process-wide pacer keeps 1.2 s between calls (the free tier is 60 a minute and the news
call shares the key). Measured: 13.4 to 16.8 s per brief without the fallback; DE and NKE with
the fallback and SPY uncached (20 calls) took 23.0 s each. The deadline is 30 s (at most
45 s). With the news (10 s) and ROIC-persistence (15 s) defaults, the three optional sections
together stay inside the research phase's slack of about 100 s. Several briefs that finish the
model call together queue on the pacer; a late one reads "unavailable".

## Not done

- **Feeding the table to the model.** Needs the A/B the plan describes.
- **Sector-relative scoring.** Changes scores; needs cross-universe rank IC.
