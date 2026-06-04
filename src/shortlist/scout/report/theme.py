"""Single source of truth for report colors + sub-score order. Dep-free (no numpy)."""
from __future__ import annotations

SUBS = ["quality", "moat", "growth", "value", "momentum", "insider", "risk"]
SUB_LABELS = {"quality": "Qual", "moat": "Moat", "growth": "Grow", "value": "Value",
              "momentum": "Mom", "insider": "Insdr", "risk": "Risk"}

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
    for (t0, c0), (t1, c1) in zip(_STOPS, _STOPS[1:]):
        if t <= t1:
            f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return tuple(round(a + (b - a) * f) for a, b in zip(c0, c1))  # type: ignore[return-value]
    return _STOPS[-1][1]


def rgb_hex(c: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % c


def text_on(c: tuple[int, int, int]) -> tuple[int, int, int]:
    """Pick dark or light text for legibility on fill `c` (luminance test)."""
    lum = 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
    return (17, 24, 31) if lum > 140 else (233, 237, 239)
