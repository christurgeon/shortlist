"""Single source of truth for report colors + sub-score order. Dep-free (no numpy)."""
from __future__ import annotations

SUBS = ["quality", "moat", "growth", "value", "momentum", "insider", "risk"]
SUB_LABELS = {"quality": "Qual", "moat": "Moat", "growth": "Grow", "value": "Value",
              "momentum": "Mom", "insider": "Insdr", "risk": "Risk"}

# Plain-English descriptions for the hard gates and soft flags that ScoreCard can
# emit — the single source of truth for the report glossary. Insertion order here
# is the glossary's display order within each group. Keep each one line; grounded
# in scoring.py:check_gates / check_flags. Any id absent from these maps still
# renders (its raw id, no description) — describe_code never raises.
GATE_DESCRIPTIONS = {
    "negative_fcf":          "Negative free cash flow (stage-aware: excused for fast, durable growers).",
    "over_leveraged":        "Net-debt/EBITDA (or a debt/equity fallback) above the safe threshold.",
    "below_min_mktcap":      "Market cap below the screen's minimum size.",
    "heavy_insider_selling": "Strongly negative insider sentiment (net selling) over the trailing window.",
}

FLAG_DESCRIPTIONS = {
    "crowded_short":            "High short interest, rising and hard to cover — squeeze / short-thesis risk.",
    "value_trap":              "Looks cheap, but quality or growth is weak.",
    "cash_burn":               "Free cash flow is negative (advisory, any magnitude).",
    "dilution":                "Persistent net share issuance (roughly +3%/yr or more).",
    "social_hype":             "Elevated and rising Reddit/WSB mention volume.",
    "news_spike":              "Elevated and rising mainstream news volume.",
    "filing_text_change":      "Large YoY change in 10-K/10-Q risk factors or MD&A (Lazy Prices). DORMANT — never fires.",
    "risk_off_regime":         "Leveraged or cyclical name during a risk-off macro regime.",
    "insider_cluster_buy":     "Multiple distinct insiders buying together.",
    "planned_sale":            "Insider sale appears pre-planned (10b5-1).",
    "recent_8k":               "Recent 8-K material-event filing.",
    "activist_13d":            "Activist investor (Schedule 13D) ownership stake filed.",
    "passive_13g":             "Passive large-holder (Schedule 13G) ownership stake filed.",
    "planned_insider_sale_144": "Form 144 notice of intent to sell.",
    "late_filing":             "NT 10-K/10-Q — the company told the SEC it could not file on time.",
    "shelf_offering":          "Shelf registration or takedown (S-3 / 424B5) — dilution capacity.",
    "sec_comment_letter":      "SEC staff correspondence on this filer's disclosures (UPLOAD/CORRESP).",
    "restatement_8k":          "8-K item 4.02 — prior financial statements should no longer be relied on.",
    "auditor_change":          "8-K item 4.01 — the independent auditor changed.",
    "listing_deficiency":      "8-K item 3.01 — exchange notice of a listing-standard failure.",
}


def describe_code(code: str) -> str:
    """Plain-English description for a gate/flag id, or '' when unknown.

    The displayed label is always the raw id itself; this supplies only the
    description, so unknown/future ids degrade gracefully (id shown, no blurb)."""
    return FLAG_DESCRIPTIONS.get(code) or GATE_DESCRIPTIONS.get(code) or ""

BG = (23, 33, 43)        # #17212b
FG = (233, 237, 239)     # #e9edef
GRID = (43, 57, 71)      # #2b3947
GRAY_BAD = (51, 64, 77)  # #33404d — None / masked cell

# RdYlGn anchors at 0 / 50 / 100.
_STOPS = [(0.0, (215, 48, 39)), (0.5, (255, 235, 130)), (1.0, (26, 152, 80))]


def score_to_rgb(v: float | None) -> tuple[int, int, int]:
    """Map a 0..100 score to an (r,g,b) tuple. None -> neutral gray. Clamps out-of-range."""
    if v is None:
        return GRAY_BAD
    t = max(0.0, min(1.0, v / 100.0))
    for (t0, c0), (t1, c1) in zip(_STOPS, _STOPS[1:], strict=False):  # adjacent-pair walk: deliberately unequal lengths
        if t <= t1:
            f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return tuple(round(a + (b - a) * f) for a, b in zip(c0, c1, strict=True))  # type: ignore[return-value]
    return _STOPS[-1][1]


def rgb_hex(c: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % c


def text_on(c: tuple[int, int, int]) -> tuple[int, int, int]:
    """Pick dark or light text for legibility on fill `c` (luminance test)."""
    lum = 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
    return (17, 24, 31) if lum > 140 else (233, 237, 239)


# Screening-call stance → fill color (RdYlGn-aligned) + traffic-light emoji.
STANCE_RGB = {
    "STRONG_BUY": (26, 152, 80),
    "BUY": (102, 189, 99),
    "HOLD": (255, 235, 130),
    "AVOID": (244, 109, 67),
    "STRONG_AVOID": (215, 48, 39),
}
_STANCE_EMOJI = {"STRONG_BUY": "🟢", "BUY": "🟢", "HOLD": "🟡",
                 "AVOID": "🔴", "STRONG_AVOID": "🔴"}


def stance_to_rgb(stance: str) -> tuple[int, int, int]:
    return STANCE_RGB.get(stance, GRAY_BAD)


def stance_emoji(stance: str) -> str:
    return _STANCE_EMOJI.get(stance, "")
