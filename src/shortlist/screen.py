from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from .coverage import build_coverage, coverage_note_line
from .env import load_env, redact_secrets
from .models import ScoreCard, rank_key
from .scoring import score


def run_harness(tickers: list[str], source_names: list[str], config: dict,
                macro=None) -> list[ScoreCard]:
    """Score via the harness stack: collect TickerSnapshots, bridge each to
    StockMetrics, then run the same scorer the screener uses. Harness cards now
    carry the same `coverage` diagnostic as screener cards, via the
    snapshot_to_coverage_inputs adapter — so per-source fetch status (gated_402,
    empty, error) and unavailable sub-scores are surfaced identically on both
    paths."""
    from .data.bridge import snapshot_to_metrics
    from .data.collector import collect
    from .data.coverage_adapt import snapshot_to_coverage_inputs

    snapshots = collect(tickers, source_names, config=config)
    cards = []
    for s in snapshots:
        card = score(snapshot_to_metrics(s), config, macro)
        outcomes, contributed = snapshot_to_coverage_inputs(s, source_names)
        card.coverage = build_coverage(outcomes, contributed, card)
        cards.append(card)
    cards.sort(key=rank_key, reverse=True)
    return cards


def _flags_cell(c: ScoreCard) -> str:
    """Combined chips for the 'Flags' column: hard gates first, then soft flags, then
    the display-only 'thin' coverage advisory."""
    chips = list(c.gates) + list(c.flags) + (["thin"] if getattr(c, "thin", False) else [])
    return ",".join(chips) or "-"


def _print_table(cards: list[ScoreCard]) -> None:
    try:
        from rich.console import Console
        from rich.table import Table
    except ImportError:
        _print_plain(cards)
        return

    table = Table(title="Moat + value screen", title_style="bold")
    _cols = [
        # (header, justify, min_width)
        ("Rank",    "right", None),
        ("Ticker",  "left",  6),
        ("Score",   "right", 5),
        ("Qual",    "right", 4),
        ("Moat",    "right", 4),
        ("Grow",    "right", 4),
        ("Momt",    "right", 4),
        ("Value",   "right", 5),
        ("Insdr",   "right", 5),
        ("Conf",    "right", 5),
        ("Risk",    "right", 5),
        ("Upside",  "right", 6),
        ("Flags",   "left",  5),
    ]
    for header, justify, min_width in _cols:
        kwargs = {"justify": justify}
        if min_width is not None:
            kwargs["min_width"] = min_width
        table.add_column(header, **kwargs)
    for i, c in enumerate(cards, 1):
        up = c.metrics.upside_to_target() if c.metrics else None
        style = "dim red" if c.gates else None
        table.add_row(
            str(i), c.ticker, f"{c.composite:.1f}",
            _f(c.quality), _f(c.moat), _f(c.growth), _f(c.momentum), _f(c.value), _f(c.insider),
            f"{c.confidence:.2f}",
            _f(c.risk),
            f"{up*100:.0f}%" if up is not None else "-",
            _flags_cell(c), style=style,
        )
    Console().print(table)


def _print_plain(cards: list[ScoreCard]) -> None:
    print(f"{'#':>2} {'TICK':<6} {'COMP':>5} {'QUAL':>5} {'MOAT':>5} {'GRW':>5} "
          f"{'MOM':>5} {'VAL':>5} {'INSD':>5} {'CONF':>5} {'RISK':>5}  FLAGS")
    for i, c in enumerate(cards, 1):
        print(f"{i:>2} {c.ticker:<6} {c.composite:>5.1f} {_f(c.quality):>5} "
              f"{_f(c.moat):>5} {_f(c.growth):>5} {_f(c.momentum):>5} {_f(c.value):>5} "
              f"{_f(c.insider):>5} {c.confidence:>5.2f} {_f(c.risk):>5}  {_flags_cell(c)}")


def _print_coverage_notes(cards: list[ScoreCard]) -> None:
    flagged = [c for c in cards if c.coverage is not None]
    if not flagged:
        return
    print("\nCoverage notes", file=sys.stderr)
    for c in flagged:
        print(coverage_note_line(c.ticker, c.coverage), file=sys.stderr)


def _f(x):
    return f"{x:.0f}" if x is not None else "-"


def _positive_int(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return n


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="shortlist")
    ap.add_argument("--tickers", help="comma-separated, e.g. GEV,LMT,SCHW,TMO,GOOGL")
    ap.add_argument("--provider", help="comma-separated source chain; overrides config harness_sources")
    ap.add_argument("--config", default=str(Path(__file__).parent.parent.parent / "config.yaml"))
    ap.add_argument("--demo", action="store_true", help="offline run on the sample basket")
    ap.add_argument("--csv", help="write ranked results to this CSV path")
    ap.add_argument("--json", action="store_true", help="emit JSON to stdout instead of a table")
    ap.add_argument("--research", type=_positive_int, metavar="N",
                    help="after ranking, generate a qualitative brief for the top N non-gated names")
    ap.add_argument("--refresh", action="store_true",
                    help="regenerate research briefs even if a cached one exists")
    ap.add_argument("--no-cache", action="store_true",
                    help="disable the on-disk HTTP cache for this run")
    ap.add_argument("--refresh-cache", action="store_true",
                    help="bypass cached HTTP responses and repopulate them")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_arg_parser()
    args = ap.parse_args(argv)

    load_env()  # pick up API keys from a .env file if present

    # Guarded config load: a missing file, empty YAML (None), or non-mapping top
    # level would otherwise surface as a raw traceback deep inside scoring. This
    # is deliberately NOT schema validation — just the three load-shape failures.
    cfg_path = Path(args.config)
    try:
        raw = cfg_path.read_text()
    except OSError as e:
        print(f"shortlist: cannot read config file {cfg_path}: {e}", file=sys.stderr)
        return 2
    try:
        config = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        print(f"shortlist: invalid YAML in config file {cfg_path}: {e}", file=sys.stderr)
        return 2
    if config is None:
        print(f"shortlist: config file {cfg_path} is empty", file=sys.stderr)
        return 2
    if not isinstance(config, dict):
        print(f"shortlist: config file {cfg_path} must be a YAML mapping, "
              f"got {type(config).__name__}", file=sys.stderr)
        return 2

    from .cache import configure_default_cache
    cache_cfg = config.get("cache", {})
    configure_default_cache(
        # --demo is offline (mock source, no HTTP), so there's nothing to cache.
        enabled=(not args.no_cache) and (not args.demo) and cache_cfg.get("enabled", True),
        refresh=args.refresh_cache,
        path=cache_cfg.get("path"),
        ttls=cache_cfg.get("ttl"),
    )

    if not args.demo and not args.tickers:
        ap.error("--tickers is required unless --demo")
    if args.demo:
        tickers = ["GEV", "LMT", "SCHW", "TMO", "GOOGL"]
    else:
        # Strip whitespace and drop empties so "AAPL, MSFT," never sends "" or
        # " MSFT" into the harness.
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        if not tickers:
            ap.error(f"--tickers {args.tickers!r} contains no ticker symbols")

    if args.demo:
        sources = ["mock"]
    elif args.provider:
        sources = [s.strip() for s in args.provider.split(",") if s.strip()]
        if not sources:
            # an all-empty value (e.g. a misexpanded shell variable) must fail
            # loudly — an empty source list would "succeed" with all-null scores
            ap.error(f"--provider {args.provider!r} contains no source names")
    else:
        sources = config.get("harness_sources",
                             ["yahoo", "fmp", "finnhub", "edgar", "finra", "wsb"])
    # --demo is offline (mock source, no HTTP) — skip the keyless FRED fetch too,
    # so the demo never makes a network call or hangs on a timeout.
    from .data.macro import fetch_macro
    macro = None if args.demo else fetch_macro(config)
    cards = run_harness(tickers, sources, config, macro=macro)
    _print_coverage_notes(cards)

    research_paths: dict = {}
    if args.research:
        research_paths = _run_research_phase(cards, config, args.research, args.refresh)

    if args.csv:
        _write_csv(cards, args.csv)
        # stderr, NOT stdout — with --json this line would corrupt the JSON stream.
        print(f"wrote {args.csv}", file=sys.stderr)
    if args.json:
        print(json.dumps([_card_dict(c, research_paths) for c in cards], indent=2))
    else:
        _print_table(cards)
    return 0


def _research_available() -> bool:
    try:
        from .research import is_available
    except ImportError:
        return False
    return is_available()


def _run_enrich(cards, config, n, refresh):
    from .research import enrich
    return enrich(cards, config, top_n=n, refresh=refresh)


def _run_research_phase(cards, config, n: int, refresh: bool) -> dict:
    """Run the qualitative research phase over the top-N non-gated cards.
    Returns {ticker: brief_path} for names that produced (or have a cached)
    brief. All console output goes to stderr so it never contaminates --json
    stdout. Never raises."""
    if not _research_available():
        print("  ! skipping research: `claude` CLI or edgartools unavailable",
              file=sys.stderr)
        return {}
    try:
        results = _run_enrich(cards, config, n, refresh)
    except Exception as e:
        print(f"  ! research phase failed: {redact_secrets(e)}", file=sys.stderr)
        return {}
    paths: dict[str, str] = {}
    total = 0.0
    if results:
        print("\nQualitative research", file=sys.stderr)
    for r in results:
        if r.skipped:
            print(f"  {r.ticker:<6} skipped: {r.skipped}", file=sys.stderr)
            continue
        paths[r.ticker] = r.brief_path
        if r.from_cache:
            print(f"  {r.ticker:<6} (cached)  {r.brief_path}", file=sys.stderr)
            continue
        total += r.cost_usd
        print(f"  {r.ticker:<6} ${r.cost_usd:.4f}  {r.brief_path}\n"
              f"           {r.synthesis}", file=sys.stderr)
    if total:
        print(f"  research cost: ${total:.4f}", file=sys.stderr)
    return paths


def _card_dict(c: ScoreCard, research_paths: dict | None = None) -> dict:
    up = c.metrics.upside_to_target() if c.metrics else None
    d = {
        "ticker": c.ticker, "composite": c.composite, "quality": c.quality,
        "moat": c.moat, "growth": c.growth, "momentum": c.momentum, "value": c.value,
        "opportunity": c.opportunity, "insider": c.insider,
        "risk": c.risk,
        "piotroski_f": c.piotroski_f,
        "piotroski_f_legs": c.piotroski_f_legs,
        "share_count_cagr": round(c.share_count_cagr, 4) if c.share_count_cagr is not None else None,
        # Investment & earnings-quality (§3); surfaced from metrics (no ScoreCard field).
        "asset_growth": (round(c.metrics.asset_growth, 4)
                         if c.metrics and c.metrics.asset_growth is not None else None),
        "accruals": (round(c.metrics.accruals, 4)
                     if c.metrics and c.metrics.accruals is not None else None),
        # Total shareholder yield (§5); surfaced from metrics (no ScoreCard field).
        "shareholder_yield": (round(c.metrics.shareholder_yield, 4)
                              if c.metrics and c.metrics.shareholder_yield is not None else None),
        # PREDICTIVE_SIGNALS §2 price-refinement measurement axes; surfaced from metrics
        # (no ScoreCard field, no production leg — backtest-only).
        "pct_to_52w_high": (round(c.metrics.pct_to_52w_high, 4)
                            if c.metrics and c.metrics.pct_to_52w_high is not None else None),
        "max_daily_return": (round(c.metrics.max_daily_return, 4)
                             if c.metrics and c.metrics.max_daily_return is not None else None),
        "vol_scaled_momentum": (round(c.metrics.vol_scaled_momentum, 4)
                                if c.metrics and c.metrics.vol_scaled_momentum is not None else None),
        # SUE inputs (§1); surfaced from metrics (no ScoreCard field). days_since_last_report
        # is an APPROXIMATION (see bridge/_earnings). The decayed SUE leg itself is in momentum.
        "earnings_surprise_dispersion": (round(c.metrics.earnings_surprise_dispersion, 3)
                                         if c.metrics and c.metrics.earnings_surprise_dispersion is not None else None),
        "earnings_days_since_last_report": (c.metrics.earnings_days_since_last_report
                                            if c.metrics else None),
        # "Lazy Prices" YoY filing-text similarity (§4); surfaced from metrics (no
        # ScoreCard field). LOW values trip the advisory filing_text_change flag.
        "filing_text_similarity": (round(c.metrics.filing_text_similarity, 4)
                                   if c.metrics and c.metrics.filing_text_similarity is not None
                                   else None),
        "ebitda": c.ebitda,
        # Display floor: net cash (signed < 0) shows as 0.0 here; the gate read the raw sign.
        "net_debt_to_ebitda": (round(max(0.0, c.net_debt_to_ebitda), 2)
                               if c.net_debt_to_ebitda is not None else None),
        "upside_to_target": round(up, 3) if up is not None else None,
        "gates": c.gates,
        "flags": c.flags,
        "sic_bucket": c.sic_bucket,
        "confidence": c.confidence,
        "scored": c.scored,
        "thin": c.thin,
    }
    if c.abstentions:
        d["abstentions"] = c.abstentions
    if c.metrics is not None and c.metrics.filing_events:
        d["events"] = {
            "recent_8k": bool(c.metrics.recent_8k),
            "activist_13d": bool(c.metrics.activist_13d),
            "passive_13g": bool(c.metrics.passive_13g),
            "planned_insider_sale_144": bool(c.metrics.planned_insider_sale_144),
            "recent": c.metrics.filing_events,
        }
    if research_paths and c.ticker in research_paths:
        d["research_path"] = research_paths[c.ticker]
    if c.coverage is not None:
        cov = {"providers": c.coverage.providers, "unavailable": c.coverage.unavailable}
        if c.coverage.note:
            cov["note"] = c.coverage.note
        d["coverage"] = cov
    return d


def _write_csv(cards: list[ScoreCard], path: str) -> None:
    import csv

    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "ticker", "composite", "quality", "moat", "growth",
                    "momentum", "value", "opportunity", "insider", "risk",
                    "upside_to_target", "gates", "scored", "confidence", "sic_bucket",
                    "piotroski_f", "share_count_cagr", "net_debt_to_ebitda"])
        for i, c in enumerate(cards, 1):
            d = _card_dict(c)
            w.writerow([i, d["ticker"], d["composite"], d["quality"], d["moat"],
                        d["growth"], d["momentum"], d["value"], d["opportunity"],
                        d["insider"], d["risk"], d["upside_to_target"],
                        "|".join(d["gates"]), d["scored"], d["confidence"], d["sic_bucket"],
                        (f'{d["piotroski_f"]}/{d["piotroski_f_legs"]}'
                         if d["piotroski_f"] is not None else ""),
                        d["share_count_cagr"], d["net_debt_to_ebitda"]])


if __name__ == "__main__":
    raise SystemExit(main())
