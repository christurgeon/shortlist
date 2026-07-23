"""Static financial glossary behind the Telegram /explain command.

Pure leaf (stdlib only -- the data/finra.py / _form4.py pattern): imports
nothing from scoring/harness/bot. Entries are SEMANTICS-ONLY by design --
they never quote config.yaml thresholds or weights, so tuning never stales
them. Each body has three beats: what the term is, what it historically
implies about a stock, and how this system treats it (scored leg / hard
gate / advisory flag / discovery-only / research-only).

Completeness is enforced, not hoped for: tests/test_scoring_names.py binds
scoring.KNOWN_GATES/KNOWN_FLAGS to the emission-site literals, and
tests/scout/test_glossary.py asserts every one of those names resolves via
lookup() -- adding a gate/flag without documenting it here fails CI.

Alias partition (collisions assert at module load): concept entries own the
bare tokens ("8k", "13d", "144"); flag entries own the exact scoring
literal ("recent_8k", "activist_13d"). "dilution" is one merged entry.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

_STRIP = re.compile(r"[\s\-_./]+")

CATEGORIES = ("SEC filings", "Sub-scores", "Gates & flags",
              "Finance concepts", "Report mechanics")


def _normalize(term: str) -> str:
    return _STRIP.sub("", term.strip().lower())


@dataclass(frozen=True)
class Entry:
    name: str
    category: str
    aliases: tuple[str, ...]
    text: str


GLOSSARY: list[Entry] = [
    # ------------------------------------------------------------- SEC filings
    Entry("13D", "SEC filings", ("sc 13d", "schedule 13d"),
          "SEC filing when an investor crosses 5% ownership WITH intent to "
          "influence (vs 13G = passive). Historically precedes positive "
          "drift: activists push operational/strategic change and "
          "re-ratings. Here: the scout surfaces fresh initial 13Ds as watch "
          "candidates, and the activist_13d flag marks one on a screened "
          "name — advisory only, never moves the score."),
    Entry("13G", "SEC filings", ("sc 13g", "schedule 13g"),
          "SEC filing when an investor crosses 5% ownership as a PASSIVE "
          "holder (no intent to influence — the quiet sibling of the 13D). "
          "Mild validation that a large holder wants exposure, but no "
          "catalyst: passive stakes don't force change. Here: the "
          "passive_13g flag notes a fresh one — context only."),
    Entry("8-K", "SEC filings", ("8k",),
          "The 'current report' — companies must file one within days of a "
          "material event (deals, financing, executive changes, defaults…). "
          "Each event has an item number. On average 8-K news skews "
          "NEGATIVE and the filing-day pop often reverses, so a fresh 8-K "
          "means 'read it', not 'buy it'. Here: the recent_8k flag notes "
          "one; certain reliably-bad items trigger the scout's negative "
          "veto (see 8-K negative-item veto)."),
    Entry("Form 4", "SEC filings", ("insider filing",),
          "Insider trade disclosure — officers, directors, and 10% owners "
          "must report their trades within two business days. Open-market "
          "BUYS are informative (insiders buy for one reason); sells are "
          "noisy (tax, diversification). Clustered buys by several insiders "
          "are the strongest configuration. Here: Form 4s feed the insider "
          "sub-score and the scout's discovery feed."),
    Entry("Form 144", "SEC filings", ("144", "rule 144"),
          "Notice of INTENT to sell restricted stock (typically insider "
          "shares). Mildly negative to neutral — often routine "
          "diversification, and the sale may never execute. Here: the "
          "planned_insider_sale_144 flag notes a fresh one — context, not "
          "a signal."),
    Entry("10-K", "SEC filings", ("10k", "annual report"),
          "The audited annual report — the bedrock disclosure: full "
          "financial statements, risk factors, MD&A. The most reliable "
          "fundamental data a company publishes. Here: 10-K financials "
          "feed quality/moat/growth/value (via EDGAR when FMP is gated), "
          "and /deep's research brief is built on the latest 10-K."),
    Entry("10-Q", "SEC filings", ("10q", "quarterly report"),
          "The quarterly report — unaudited, thinner than the 10-K, but "
          "three months fresher. Watch quarter-over-quarter deterioration "
          "the annual numbers haven't caught yet. Here: the latest 10-Q's "
          "MD&A rides the /deep research brief alongside the 10-K."),
    Entry("DEF 14A", "SEC filings", ("proxy", "proxy statement"),
          "The annual proxy statement — executive pay, board structure, "
          "and who owns 5%+ blocks. Extreme pay-versus-performance "
          "misalignment or heavy control concentration is a governance "
          "yellow flag (founder control cuts both ways). Here: read "
          "per-/deep as a caveated context line — never scored."),
    Entry("10b5-1 plan", "SEC filings", ("10b5-1", "trading plan"),
          "A pre-scheduled insider trading plan adopted while the insider "
          "has no inside information — trades execute automatically later. "
          "Planned sells carry far less information than discretionary "
          "ones. Here: 10b5-1 sales are forgiven in the insider SCORE, but "
          "never excuse the heavy_insider_selling GATE."),
    Entry("20-F", "SEC filings", ("20f",),
          "The annual report for foreign private issuers (ADRs) — the "
          "10-K's overseas cousin, on a different template. Fundamentals "
          "for these names are often thinner in US data feeds. Here: /deep "
          "briefs are 10-K-only, so foreign issuers get an ADR-aware skip "
          "rather than a wrong-document brief."),
    Entry(
        name="8k_negative",
        category="SEC filings",
        aliases=("8-k negative", "negative 8-k", "clean-negative 8-k",
                 "8k item 1.03", "8k item 2.04", "8k item 4.02"),
        text=("A clean-negative 8-K is a current report announcing an unambiguously bad, "
              "dated event: bankruptcy (item 1.03), a lender calling debt due early (2.04), "
              "or that past financial statements can no longer be relied on — a restatement "
              "(4.02). The position monitor surfaces one against a name you own as an "
              "attention flag routed to the SEC filing; it is screening triage, not advice, "
              "and never a recommendation to sell."),
    ),
    # -------------------------------------------------------------- Sub-scores
    Entry("quality", "Sub-scores", ("quality score",),
          "0–100: how good the business engine is — margins, returns on "
          "capital, and earnings quality (an inverted accruals leg: cash "
          "should back the earnings). High-quality compounders survive "
          "drawdowns and re-rate; junk mean-reverts. Structurally "
          "undefined legs (e.g. gross margin for a bank) are masked per "
          "sector rather than scored wrong."),
    Entry("moat", "Sub-scores", ("moat score",),
          "0–100: durability proxies — margin stability and consistency "
          "standing in for pricing power and competitive insulation. A "
          "wide moat means competitors can't easily compress returns. "
          "Masked for financials/REITs where the proxies are undefined; "
          "that's why a bank shows a null moat."),
    Entry("growth", "Sub-scores", ("growth score",),
          "0–100: revenue and EPS trajectory — multi-year CAGR plus "
          "persistence (how many years actually grew). Persistent growth "
          "is worth more than one hot year. Fast persistent growers can "
          "also be excused from the negative-FCF gate (investing ahead of "
          "cash flow is a stage, not a defect)."),
    Entry("value", "Sub-scores", ("value score",),
          "0–100: cheapness — FCF yield, PEG, and price versus the name's "
          "own history. Cheapness alone is a weak signal (see value_trap); "
          "cheap PLUS quality is the combination that pays. Weighted well "
          "above momentum in the composite. FMP free-plan gating often "
          "nulls parts of this axis — check coverage before reading a "
          "null value as 'expensive'."),
    Entry("momentum", "Sub-scores", ("momentum score", "price momentum"),
          "0–100: price trend — the 12-month-minus-1 momentum family, "
          "including a residual (market-stripped) leg. Winners keep "
          "winning over months; the latest month is skipped because it "
          "mean-reverts. Short-horizon by nature — a tilt for timing, not "
          "a thesis. The smaller sibling of value in the composite."),
    Entry("insider", "Sub-scores", ("insider score",),
          "0–100: what insiders are doing with their own money — net "
          "Form-4 buying vs selling, with buys weighted by role (a CFO "
          "buying beats a director). Insider buys predict returns far "
          "better than sells predict trouble. Cluster buys can only raise "
          "this score, never lower it; 10b5-1 planned sells are forgiven."),
    Entry("risk", "Sub-scores", ("risk score",),
          "0–100, higher = calmer: inverted realized volatility and max "
          "drawdown. A composite-only tilt toward names that hurt less to "
          "hold — but trailing risk peaks at bottoms, so it can be "
          "anti-predictive at turning points. Deliberately excluded from "
          "the confidence/scored bookkeeping."),
    Entry("composite", "Sub-scores", ("score", "overall score"),
          "The headline 0–100 number: a weighted blend of the seven "
          "sub-scores (quality, moat, growth, value, momentum, insider, "
          "risk). Weights are ratios — only their proportions matter. When "
          "a sub-score has no inputs its weight is redistributed across the "
          "rest, never silently zeroed, so a thin name isn't quietly "
          "dragged down. Rank with it, but read gates/flags/confidence "
          "alongside it."),
    Entry("opportunity", "Sub-scores", ("opportunity score",),
          "DISPLAY-ONLY: max(momentum, value) — which door is open, the "
          "trend door or the cheap door. It does NOT feed the composite "
          "(value and momentum are weighted independently there). Read it "
          "as 'what kind of setup is this', not as a ranking input."),
    # ------------------------------------------------------------ Gates & flags
    Entry("gates vs flags", "Gates & flags", ("gate", "gates", "flag", "flags"),
          "Gates are HARD disqualifiers — a tripped gate means the name "
          "cannot pass or top the ranking regardless of score (shown as "
          "'gated'). Flags are SOFT advisories — context worth knowing "
          "(crowded short, value trap, social hype…) that never affects "
          "passed/composite/scored. Read gates as 'no', flags as 'but "
          "note…'."),
    Entry("negative_fcf", "Gates & flags", (),
          "GATE: the latest fiscal year's free cash flow is negative — the "
          "business consumed cash after reinvestment. Chronic burners "
          "depend on markets for oxygen. Excused for fast, persistent "
          "growers (investing ahead of cash flow); the cash_burn flag "
          "still shows regardless. Masked for financials, where FCF is "
          "structurally undefined."),
    Entry("below_min_mktcap", "Gates & flags", ("min market cap",),
          "GATE: market capitalization below the screen's floor. Micro-cap "
          "data is unreliable, spreads are wide, and manipulation risk is "
          "real — too small to trust the numbers, whatever the score "
          "says."),
    Entry("over_leveraged", "Gates & flags", ("leverage gate",),
          "GATE: debt too heavy for the earnings power — primarily "
          "net-debt/EBITDA (years of earnings to repay debt), with a "
          "debt-to-equity fallback that is artifact-guarded so buyback "
          "compounders with thin book equity aren't punished while "
          "genuinely distressed levered names are caught. Leverage turns "
          "downturns into death spirals."),
    Entry("heavy_insider_selling", "Gates & flags", ("insider selling",),
          "GATE: insider sentiment is strongly negative — the people with "
          "the best information are net heavy sellers. One sale means "
          "little; a broad selling skew is the bearish configuration. "
          "10b5-1 planned sales soften the insider SCORE but never excuse "
          "this gate."),
    Entry("crowded_short", "Gates & flags", (),
          "FLAG: high short interest plus high days-to-cover, rising, on "
          "fresh data. Two-sided: squeeze fuel if good news lands, but "
          "shorts are informed on average, so heavy shorting has a "
          "NEGATIVE base rate. Advisory only — the fundamental axes decide "
          "whether the shorts look wrong here."),
    Entry("value_trap", "Gates & flags", (),
          "FLAG: the name scores cheap while quality or growth is weak — "
          "'cheap for a reason'. Falling knives look like bargains all the "
          "way down. A Piotroski refinement can suppress the flag on "
          "cheap-but-IMPROVING names and confirm it on deteriorating ones. "
          "Advisory only."),
    Entry("dilution", "Gates & flags",
          ("share issuance", "share count cagr", "buybacks", "buyback"),
          "FLAG + concept: persistent net share issuance — the share count "
          "compounds UP, so each share's claim on the business shrinks and "
          "per-share results lag company results. Positive share-count "
          "CAGR = dilution; negative = buybacks (the shareholder-friendly "
          "direction). Advisory only."),
    Entry("cash_burn", "Gates & flags", ("burn",),
          "FLAG: free cash flow is negative, full stop — shown even when "
          "the negative_fcf gate excuses a fast grower. Burn means the "
          "clock is running: check the cash runway and whether the burn is "
          "buying growth or just happening. Advisory only."),
    Entry("social_hype", "Gates & flags", ("wsb", "reddit"),
          "FLAG: elevated and rising WallStreetBets mention volume. Retail "
          "attention spikes mark crowded, sentiment-driven names and tend "
          "to mean-revert. Next to crowded_short it reads as squeeze "
          "chatter; next to value_trap, as pump caution. Advisory only."),
    Entry("news_spike", "Gates & flags", (),
          "FLAG: elevated and rising mainstream press volume (distinct "
          "from WSB chatter). A spike is meaningful for a normally-quiet "
          "name — something is happening — so it's suppressed on "
          "always-noisy mega-caps where counts are capped anyway. "
          "Advisory only."),
    Entry("risk_off_regime", "Gates & flags", ("risk off",),
          "FLAG: the macro regime is risk-off AND this name is exposed — "
          "leveraged or in a cyclical sector. Those are the names "
          "drawdowns hit hardest when the tide goes out. Regime-dependent "
          "context, not a verdict on the business. Advisory only."),
    Entry("recent_8k", "Gates & flags", (),
          "FLAG: a fresh 8-K material-event filing exists. Presence-based "
          "— it says 'something happened, go read it', not whether it was "
          "good. The average 8-K skews negative, so treat it as homework, "
          "not endorsement. Advisory only. See 8-K."),
    Entry("activist_13d", "Gates & flags", ("activist",),
          "FLAG: a fresh activist 13D stake exists on this name — an "
          "investor crossed 5% with intent to influence. Historically a "
          "positive re-rating catalyst (the edge is the post-filing drift, "
          "not the filing-day pop). Advisory only. See 13D."),
    Entry("passive_13g", "Gates & flags", (),
          "FLAG: a fresh passive 5% stake (13G) exists. A large holder "
          "wants exposure — mild validation, no catalyst, since passive "
          "holders don't force change. Advisory only. See 13G."),
    Entry("planned_insider_sale_144", "Gates & flags", (),
          "FLAG: a fresh Form 144 — an insider filed INTENT to sell "
          "restricted stock. Usually routine diversification and the sale "
          "may never execute; worth a glance, not alarm. Advisory only. "
          "See Form 144."),
    Entry("insider_cluster_buy", "Gates & flags", ("cluster buy",),
          "FLAG: several distinct insiders bought recently — the strongest "
          "insider configuration, since independent wallets rarely agree "
          "by accident. Historically the most predictive insider signal. "
          "Can only help the insider score, never hurt it. Advisory only."),
    Entry("planned_sale", "Gates & flags", (),
          "FLAG: substantial 10b5-1 planned-sale value is pending — "
          "scheduled, low-information selling (the plan was adopted before "
          "any inside knowledge). Noted so a coming sale doesn't surprise "
          "you; carries far less signal than a discretionary sale. "
          "Advisory only."),
    Entry("filing_text_change", "Gates & flags", ("lazy prices",),
          "FLAG: the 10-K/10-Q risk-factor + MD&A language was rewritten "
          "heavily year-over-year. Companies leave boilerplate alone until "
          "something changes — and big rewrites predict NEGATIVE returns "
          "('Lazy Prices'). Fires on LOW text similarity. Advisory only."),
    Entry("8-K negative-item veto", "Gates & flags",
          ("negative veto", "veto", "vetoed", "8k veto"),
          "Scout-funnel drop, not a score: certain 8-K items — "
          "going-concern doubt, defaults, delisting notices, restatements, "
          "auditor exits — are reliably negative over the following weeks, "
          "so a fresh match ejects the candidate LOUDLY before it burns a "
          "deep-screen slot. You'll see 'VETOED: <ticker> — 8-K item …' in "
          "the run notes."),
    # --------------------------------------------------------- Finance concepts
    Entry("CAGR", "Finance concepts", ("compound annual growth rate",),
          "Compound annual growth rate: the smoothed yearly growth between "
          "two endpoints, (end/start)^(1/years) − 1. Report variants: "
          "revenue/FCF/EPS CAGR (higher is better) and share-count CAGR "
          "(LOWER is better — positive means dilution, negative means "
          "buybacks). Endpoint-sensitive: one distorted terminal year can "
          "flatter or hide the trend, so cross-check persistence."),
    Entry("short interest", "Finance concepts", ("short percent",),
          "Shares sold short as a fraction of shares outstanding — how "
          "much money is betting against the name. Shorts are informed on "
          "average: heavy or RISING short interest has a negative base "
          "rate, and the jump is sharper than the level. Here: feeds "
          "crowded_short and the scout's short-interest jump scan — "
          "attention, with the scorer deciding direction."),
    Entry("days to cover", "Finance concepts", ("dtc", "short ratio"),
          "Short interest ÷ average daily volume: how many days of normal "
          "trading shorts would need to exit. High DTC is squeeze fuel — "
          "and, empirically, a STRONGER negative predictor than the short "
          "level itself (crowded exits are slow exits). One leg of the "
          "crowded_short flag."),
    Entry("free cash flow", "Finance concepts", ("fcf",),
          "Operating cash flow minus capital expenditure: the cash the "
          "business actually throws off after maintaining and growing "
          "itself. Much harder to dress up than EPS. Negative FCF = the "
          "business consumes cash (see negative_fcf, cash_burn). The "
          "denominator discipline behind the value axis."),
    Entry("FCF yield", "Finance concepts", ("free cash flow yield",),
          "Free cash flow ÷ market cap — the cash return you'd earn owning "
          "the whole company at today's price. The value axis's core "
          "cheapness leg: high yield is cheap IF the FCF is durable; a "
          "fading business can sport a high yield on its way down. "
          "Undefined for financials (masked)."),
    Entry("PEG", "Finance concepts", ("peg ratio",),
          "P/E ratio ÷ expected earnings growth: what you pay per unit of "
          "growth. Below ~1 is the classic 'growth at a reasonable price' "
          "zone. Depends on analyst growth estimates, which are routinely "
          "too optimistic — treat as a screen, not a valuation. One value "
          "leg (needs FMP; nulls when gated)."),
    Entry("ROIC", "Finance concepts", ("return on invested capital",),
          "After-tax operating profit ÷ invested capital: the return the "
          "business earns on the money put into it. Persistently high ROIC "
          "is the compounding engine — and maintaining it against "
          "competition is moat evidence. A quality leg; equity-centric, so "
          "masked for banks/insurers."),
    Entry("net debt / EBITDA", "Finance concepts",
          ("net debt to ebitda", "leverage"),
          "(Total debt − cash) ÷ EBITDA: roughly how many years of "
          "earnings it would take to repay the debt. The cleaner leverage "
          "lens (debt-to-equity breaks on buyback-shrunken equity). "
          "Negative = net cash. Primary metric behind the over_leveraged "
          "gate and the risk_off_regime exposure test."),
    Entry("PEAD", "Finance concepts",
          ("post earnings announcement drift", "earnings drift", "drift"),
          "Post-earnings-announcement drift: after a genuine earnings "
          "surprise, prices keep moving in the surprise's direction for "
          "weeks to months — the market underreacts to fundamental news. "
          "One of the oldest, most replicated anomalies. Basis of the SUE "
          "leg and the reason beat consistency shows in /deep briefs."),
    Entry("Piotroski F-score", "Finance concepts", ("piotroski", "f score"),
          "A 0–9 checklist of fundamental IMPROVEMENT — profitability, "
          "leverage, and efficiency each getting better or worse. Cheap "
          "stocks with high F-scores historically outperform cheap ones "
          "with low scores: improvement separates bargains from traps. "
          "Here: a Piotroski fraction refines the value_trap flag."),
    Entry("accruals", "Finance concepts", (),
          "(Net income − operating cash flow) ÷ assets: how much of "
          "earnings is paper rather than cash. High accruals predict "
          "NEGATIVE returns (Sloan) — accounting optimism mean-reverts. "
          "One of the few signals backtest-validated on this system's own "
          "data; rides quality as an inverted leg. Masked for financials."),
    Entry("drawdown", "Finance concepts", ("max drawdown",),
          "The peak-to-trough decline over the lookback window — the worst "
          "'bought the top' outcome holding the name. Deep-drawdown stocks "
          "are either broken or violently repricing; either way holding "
          "them is hard. Inverted into the risk sub-score (calmer scores "
          "higher)."),
    Entry("residual momentum", "Finance concepts", (),
          "The 12-1 momentum of what's left after stripping the market's "
          "contribution from a stock's returns (CAPM residuals) — trend "
          "the stock earned on its own, not by riding beta. Steadier than "
          "raw momentum and the only new signal that survived this "
          "system's live-price backtest; a live momentum leg. Short-lived "
          "— the edge decays past a few months."),
    Entry("SUE", "Finance concepts",
          ("standardized unexpected earnings", "earnings surprise"),
          "Standardized unexpected earnings: the latest earnings surprise "
          "÷ the firm's own surprise volatility — a beat measured against "
          "how surprising this company usually is. Big SUE drifts upward "
          "for a quarter (see PEAD), decaying toward the next report. A "
          "momentum leg here (ships off pending measurement); abstains "
          "when history is too short to standardize."),
    # -------------------------------------------------------- Report mechanics
    Entry("confidence", "Report mechanics", (),
          "The fraction of applicable sub-score weight actually present for "
          "this name — how much of the scorecard the data could fill in. "
          "Low confidence means the composite rests on few inputs (often "
          "FMP free-plan gating; see coverage). Very low confidence drops "
          "the name below the 'scored' validity floor entirely."),
    Entry("scored", "Report mechanics", (),
          "Whether confidence cleared the validity floor — enough of the "
          "applicable scorecard was present to trust the composite at all. "
          "A not-scored name cannot pass, rank on top, or be selected for "
          "research, whatever its number says. Sector-masked legs don't "
          "count against it (they're inapplicable, not missing)."),
    Entry("thin", "Report mechanics", (),
          "Advisory that confidence sits below the ranking comfort band: "
          "the name IS scored, but on a thinner data base than its "
          "neighbors — treat its rank as tentative and check coverage for "
          "which source dropped out."),
    Entry("abstention", "Report mechanics",
          ("masked", "sector masking", "abstain", "masking"),
          "A leg or gate that is structurally UNDEFINED for the sector — "
          "FCF or gross margin for a bank, leverage gates for an insurer — "
          "is excluded explicitly instead of silently averaged in wrong. "
          "Distinct from missing data: masked-inapplicable nulls don't "
          "count against coverage or scored. Why financials show null "
          "moat/value legs."),
    Entry("coverage", "Report mechanics", ("coverage note",),
          "Per-source fetch diagnostic: which providers answered, which "
          "were gated, rate-limited, or empty, and which output fields "
          "went null because of it. A null value usually reads 'FMP gated "
          "this symbol on the free plan', not 'the company has no value'. "
          "Check it before believing any null."),
    Entry("screening call", "Report mechanics",
          ("call", "buy hold avoid", "stance"),
          "The /deep brief's buy/hold/avoid stance with conviction, "
          "authored by Claude but bounded by deterministic guards: a "
          "tripped gate can only push it more bearish, and thin data caps "
          "conviction. Screening TRIAGE — where to spend your own research "
          "time — not investment advice, and not a backtested signal."),
    Entry("gated", "Report mechanics", (),
          "At least one hard gate tripped (negative FCF, over-leverage, "
          "sub-scale market cap, heavy insider selling) — the name is "
          "disqualified from passing regardless of composite. Gates are "
          "the 'no' list; see gates vs flags."),
]

_BY_ALIAS: dict[str, Entry] = {}
for _e in GLOSSARY:
    for _key in (_e.name, *_e.aliases):
        _norm = _normalize(_key)
        assert _norm not in _BY_ALIAS or _BY_ALIAS[_norm] is _e, \
            f"glossary alias collision: {_key!r}"
        _BY_ALIAS[_norm] = _e


def lookup(term: str) -> Entry | None:
    return _BY_ALIAS.get(_normalize(term))


def suggest(term: str, n: int = 3) -> list[str]:
    """Closest entry NAMES for an unknown term (deduped, match order kept)."""
    hits = difflib.get_close_matches(_normalize(term), list(_BY_ALIAS), n=n)
    names: list[str] = []
    for h in hits:
        name = _BY_ALIAS[h].name
        if name not in names:
            names.append(name)
    return names


def index_text() -> str:
    lines = ["📖 /explain <term> — glossary. Known terms:"]
    for cat in CATEGORIES:
        names = [e.name for e in GLOSSARY if e.category == cat]
        if names:
            lines.append(f"\n{cat}:\n" + ", ".join(names))
    return "\n".join(lines)


def entry_text(entry: Entry) -> str:
    return f"{entry.name}\n{entry.text}"
