"""Is "change in share of SIC-3 revenue" a measure of stability, or of size inside the industry?

Evidence for amendment 4 of docs/audits/2026-10-04-moat-durability-prereg.md. SYNTHETIC ONLY: no
SEC data, no network, a few seconds. Run from the repo root:

    uv run python docs/audits/scripts/probe_durability_share_form.py

Each world is 400 industries of 5 to 60 firms. Firm size is lognormal and three-year growth is
lognormal and INDEPENDENT of size, so no form of the predictor should be related to a firm's
share of its industry. The parameters are invented; the sign and rough size of the result do
not depend on them (three settings are printed).

READ THIS BEFORE QUOTING A NUMBER. The absolute change in share grows with the share itself, so
ranking firms by it ranks them by size inside the industry. The registered predictor was that
form until amendment 4 replaced it with the log ratio.
"""
import math
import random

from shortlist.backtest.durability_study import avg_ranks

SEED = 20261007


def spearman(a: list[float], b: list[float]) -> float:
    ra, rb = avg_ranks(dict(enumerate(a))), avg_ranks(dict(enumerate(b)))
    xa, xb = [ra[i] for i in range(len(a))], [rb[i] for i in range(len(b))]
    ma, mb = sum(xa) / len(xa), sum(xb) / len(xb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(xa, xb, strict=True))
    return cov / math.sqrt(sum((x - ma) ** 2 for x in xa) * sum((y - mb) ** 2 for y in xb))


def world(rng: random.Random, size_sd: float, growth_sd: float) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {k: [] for k in ("absolute", "log_ratio", "leave_one_out",
                                                   "share", "revenue")}
    for _ in range(400):
        n = rng.randint(5, 60)
        base = [math.exp(rng.gauss(0, size_sd)) for _ in range(n)]
        now = [b * math.exp(rng.gauss(0.1, growth_sd)) for b in base]
        tot_base, tot_now = sum(base), sum(now)
        for b, x in zip(base, now, strict=True):
            out["absolute"].append(-abs(x / tot_now - b / tot_base))             # as first registered
            out["log_ratio"].append(-abs(math.log((x / tot_now) / (b / tot_base))))      # adopted
            out["leave_one_out"].append(                                         # not adopted
                -abs(math.log(x / b) - math.log((tot_now - x) / (tot_base - b))))
            out["share"].append(x / tot_now)
            out["revenue"].append(x)
    return out


def main() -> None:
    rng = random.Random(SEED)
    print("rank correlation of each form with the firm's share of its industry (and its revenue)")
    print(f"{'size sd':>8s} {'growth sd':>10s} {'firms':>6s} | {'absolute':>16s} {'log ratio':>16s} "
          f"{'leave-one-out':>16s}")
    for size_sd, growth_sd in ((1.5, 0.25), (2.0, 0.25), (2.0, 0.40)):
        w = world(rng, size_sd, growth_sd)
        cells = [f"{spearman(w[k], w['share']):+.2f} ({spearman(w[k], w['revenue']):+.2f})"
                 for k in ("absolute", "log_ratio", "leave_one_out")]
        print(f"{size_sd:8.1f} {growth_sd:10.2f} {len(w['share']):6d} | "
              + " ".join(f"{c:>16s}" for c in cells))


if __name__ == "__main__":
    main()
