"""Composite-weight probe: does each composite axis earn its weight, and does a
pre-registered alternative weighting beat the shipped one?

Evidence for docs/audits/2026-09-25-risk-tilt-disable.md. Run from the repo root with the
Yahoo + companyfacts caches warm (a `shortlist-backtest --source xbrl` run warms both):

    set -a && . ./.env && set +a
    uv run python docs/audits/scripts/probe_composite_weights.py largecap > lc.json
    uv run python docs/audits/scripts/probe_composite_weights.py smallmid > sm.json

Axes are the ones reconstructable point-in-time: quality/moat/growth/value from SEC
companyfacts (value = fcf_yield + pe_vs_history; peg and upside_to_target need analyst
history nobody stores) and momentum/risk from price history. insider is absent, and the
composite renormalizes over present axes exactly as scoring.score() does.

Per-date XS IC uses the engine's own breadth floor (_TRUST_MIN_BREADTH), so the per-signal
means here reproduce `shortlist-backtest`'s xs_ic. Without the floor, small/mid's early,
thin dates flip the composite's sign — check the floor before trusting any paired number.

The weights in the variants below are overridden explicitly, so the probe measures the
same thing whatever config.yaml currently ships. The shipped weights at the time of
measurement were `cur`.
"""
import asyncio
import json
import sys
from collections import Counter
from datetime import date, datetime, timezone
from statistics import mean, stdev

from shortlist import scoring
from shortlist.backtest.cli import _grid_start, _load_companyfacts, _load_histories, _per_date_ic
from shortlist.backtest.engine import _TRUST_MIN_BREADTH, _collect_rows, observation_grid
from shortlist.backtest.metrics import cross_signal_xs_corr
from shortlist.backtest.signals import Observation, XbrlSignalSource
from shortlist.backtest.universe import load_universe
from shortlist.backtest.xbrl import cik_for, read_companyfacts_cache
from shortlist.config import load_config
from shortlist.data.bridge import snapshot_to_metrics
from shortlist.data.sources.yahoo_prices import snapshot_from_closes_dated
from shortlist.env import load_env

# Pre-registered 2026-09-25, before any composite result was seen.
VARIANTS = {
    "cur": {"quality": .18, "moat": .18, "growth": .135, "value": .22, "momentum": .08, "risk": .10},
    "no_risk": {"quality": .18, "moat": .18, "growth": .135, "value": .22, "momentum": .08},
    "equal": {"quality": 1, "moat": 1, "growth": 1, "value": 1, "momentum": 1, "risk": 1},  # 1/N benchmark
    "lit": {"quality": .10, "moat": .15, "growth": .15, "value": .25, "momentum": .15, "risk": .05},
    "fund": {"quality": .18, "moat": .18, "growth": .135, "value": .22},
}
# Pre-registered second round: the momentum axis rebuilt from the residual leg alone.
RESID_VARIANTS = {"cur_r": "cur", "no_risk_r": "no_risk"}
PAIRS = [("comp_no_risk", "comp_cur"), ("comp_cur_r", "comp_cur"),
         ("comp_no_risk_r", "comp_no_risk"), ("comp_fund", "comp_no_risk"),
         ("momentum_r", "momentum")]
AXES = ("quality", "moat", "growth", "value", "momentum", "risk")
HORIZONS = (1, 3, 6, 12)


def _blend(sig, weights, momentum_key="momentum"):
    parts = []
    for axis, w in weights.items():
        key = momentum_key if axis == "momentum" else axis
        if key in sig:
            parts.append((sig[key], w))
    den = sum(w for _, w in parts)
    return sum(s * w for s, w in parts) / den if den else None


class CompositeSource:
    name = "composite"

    def __init__(self, xsrc, hists, spy, t):
        self.x, self.h, self.spy, self.t = xsrc, hists, spy, t

    def observe(self, ticker, as_of):
        t = self.t
        xo = self.x.observe(ticker, as_of)
        sig = {k: v for k, v in (xo.signals if xo else {}).items()
               if k in ("quality", "moat", "growth", "value")}
        # Every variant must rest on >=1 fundamental axis (the min_composite_components
        # analogue), which also keeps all variants on the same name set per date.
        if not sig:
            return None
        hist = self.h.get(ticker.upper())
        if hist is not None:
            d, c = hist.through(as_of)
            if len(c) >= 200:
                sd, sc = self.spy.through(as_of)
                m = snapshot_to_metrics(snapshot_from_closes_dated(ticker.upper(), d, c, sd, sc))
                # Production momentum legs; eps_revision has no history, so it is absent.
                resid = scoring._norm(m.residual_momentum, *t["residual_momentum"])
                legs = [scoring._norm(m.price_vs_200dma, *t["price_vs_200dma"]),
                        scoring._norm(m.rel_strength_6m, *t["rel_strength_6m"]), resid]
                legs = [x for x in legs if x is not None]
                if legs:
                    sig["momentum"] = mean(legs)
                if resid is not None:
                    sig["momentum_r"] = resid
                for name, value, key in (("risk_vol", m.realized_vol, "realized_vol"),
                                         ("risk_dd", m.max_drawdown, "max_drawdown")):
                    v = scoring._norm(value, *t[key])
                    if v is not None:
                        sig[name] = v
                r = scoring.risk_score(m, t)
                if r is not None:
                    sig["risk"] = r
        for name, w in VARIANTS.items():
            v = _blend(sig, w)
            if v is not None:
                sig[f"comp_{name}"] = v
        for name, base in RESID_VARIANTS.items():
            v = _blend(sig, VARIANTS[base], momentum_key="momentum_r")
            if v is not None:
                sig[f"comp_{name}"] = v
        return Observation(as_of, ticker.upper(), sig)


def _floored_ic(rows):
    per_date = Counter(r[0] for r in rows)
    return _per_date_ic([r for r in rows if per_date[r[0]] >= _TRUST_MIN_BREADTH])


def _stats(xs):
    if len(xs) < 3:
        return None
    s = stdev(xs)
    return {"n": len(xs), "mean": round(mean(xs), 4),
            "t": round(mean(xs) / (s / len(xs) ** 0.5), 2) if s else None,
            "hit": round(sum(x > 0 for x in xs) / len(xs), 3)}


def main():
    load_env()
    tickers = load_universe(sys.argv[1])
    today = datetime.now(tz=timezone.utc).date().isoformat()
    t = load_config("config.yaml", required_keys=("thresholds",))["thresholds"]
    hists, spy = asyncio.run(_load_histories(tickers, ".cache/yahoo", today))
    hists = {k: v for k, v in hists.items() if v.dates}
    _, idx = asyncio.run(_load_companyfacts(tickers, ".cache/sec_xbrl", today[:7]))

    def loader(tk):
        return read_companyfacts_cache(cik_for(tk, idx), cache_dir=".cache/sec_xbrl",
                                       month=today[:7])

    src = CompositeSource(XbrlSignalSource(None, hists, t, fact_loader=loader), hists, spy, t)
    start = _grid_start(min(min(h.dates) for h in hists.values()))
    end = date.fromisoformat(today)
    out = {"universe": sys.argv[1], "universe_n": len(hists), "price_asof": today,
           "xs_ic": {}, "paired": {}}
    for h in HORIZONS:
        rows = _collect_rows(src, sorted(hists), hists, spy, observation_grid(start, end, h),
                             h, "excess")
        ics = {k: _floored_ic(v) for k, v in rows.items()}
        for k, by_date in sorted(ics.items()):
            out["xs_ic"][f"h{h} {k}"] = _stats(list(by_date.values()))
        for a, b in PAIRS:
            ds = sorted(set(ics.get(a, {})) & set(ics.get(b, {})))
            out["paired"][f"h{h} {a} - {b}"] = _stats([ics[a][x] - ics[b][x] for x in ds])
    obs = [o for o in (src.observe(tk, d) for tk in sorted(hists)
                       for d in observation_grid(start, end, 3)) if o is not None]
    out["axis_corr"] = {f"{a}~{b}": cross_signal_xs_corr(obs, a, b)
                        for i, a in enumerate(AXES) for b in AXES[i + 1:]}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
