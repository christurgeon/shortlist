from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from .coverage import build_coverage, classify_failure, coverage_note_line
from .env import load_env, redact_secrets
from .merge import merge
from .models import ScoreCard
from .providers import build_providers
from .scoring import score


def run(tickers: list[str], provider_names: list[str], config: dict) -> list[ScoreCard]:
    providers = []
    for name in provider_names:
        try:
            providers.extend(build_providers([name], config))
        except Exception as e:  # missing key or uninstalled SDK -> skip the source
            print(f"  ! skipping provider '{name}': {redact_secrets(e)}", file=sys.stderr)
    if not providers:
        print("No usable providers. Set API keys or use --demo.", file=sys.stderr)
        return []
    cards: list[ScoreCard] = []
    for t in tickers:
        per_provider = []
        outcomes: dict[str, str] = {}        # reset per ticker — must not leak
        for p in providers:
            try:
                per_provider.append(p.fetch(t))
                outcomes[p.name] = "ok"
            except Exception as e:  # one bad source shouldn't kill the run
                outcomes[p.name] = classify_failure(e)
                print(f"  ! {p.name} failed for {t}: {redact_secrets(e)}", file=sys.stderr)
        if not per_provider:
            continue
        # A provider "contributed" if its OWN fetch stamped at least one field
        # (its sources dict). Judged pre-merge so a provider that returned data
        # but lost every field to a higher-priority source isn't mislabeled empty.
        contributed = {src for m in per_provider for src in m.sources.values()}
        card = score(merge(per_provider), config)
        card.coverage = build_coverage(outcomes, contributed, card)
        cards.append(card)
    cards.sort(key=lambda c: (c.scored, c.composite), reverse=True)
    return cards


def run_harness(tickers: list[str], source_names: list[str], config: dict) -> list[ScoreCard]:
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
        card = score(snapshot_to_metrics(s), config)
        outcomes, contributed = snapshot_to_coverage_inputs(s, source_names)
        card.coverage = build_coverage(outcomes, contributed, card)
        cards.append(card)
    cards.sort(key=lambda c: (c.scored, c.composite), reverse=True)
    return cards


def _flags_cell(c: ScoreCard) -> str:
    """Combined chips for the 'Flags' column: hard gates first, then soft flags."""
    return ",".join(list(c.gates) + list(c.flags)) or "-"


def _print_table(cards: list[ScoreCard]) -> None:
    try:
        from rich.console import Console
        from rich.table import Table
    except ImportError:
        _print_plain(cards)
        return

    table = Table(title="Moat + opportunity screen", title_style="bold")
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
            f"{up*100:.0f}%" if up is not None else "-",
            _flags_cell(c), style=style,
        )
    Console().print(table)


def _print_plain(cards: list[ScoreCard]) -> None:
    print(f"{'#':>2} {'TICK':<6} {'COMP':>5} {'QUAL':>5} {'MOAT':>5} {'GRW':>5} "
          f"{'MOM':>5} {'VAL':>5} {'INSD':>5}  FLAGS")
    for i, c in enumerate(cards, 1):
        print(f"{i:>2} {c.ticker:<6} {c.composite:>5} {_f(c.quality):>5} "
              f"{_f(c.moat):>5} {_f(c.growth):>5} {_f(c.momentum):>5} {_f(c.value):>5} "
              f"{_f(c.insider):>5}  {_flags_cell(c)}")


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
    ap.add_argument("--provider", help="comma-separated provider/source chain; overrides config")
    ap.add_argument("--engine", choices=["screener", "harness"], default="screener",
                    help="screener = synchronous providers (default); "
                         "harness = async sources + TickerSnapshot bridge")
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

    config = yaml.safe_load(Path(args.config).read_text())

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
        tickers = [t.strip().upper() for t in args.tickers.split(",")]

    if args.engine == "harness":
        if args.demo:
            sources = ["mock"]
        elif args.provider:
            sources = args.provider.split(",")
        else:
            sources = config.get("harness_sources", ["yahoo", "fmp", "finnhub", "edgar"])
        cards = run_harness(tickers, sources, config)
    else:
        if args.demo:
            providers = ["mock"]
        elif args.provider:
            providers = args.provider.split(",")
        else:
            providers = config.get("providers", ["fmp"])
        cards = run(tickers, providers, config)
    _print_coverage_notes(cards)

    research_paths: dict = {}
    if args.research:
        research_paths = _run_research_phase(cards, config, args.research, args.refresh)

    if args.csv:
        _write_csv(cards, args.csv)
        print(f"wrote {args.csv}")
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
        "upside_to_target": round(up, 3) if up is not None else None,
        "gates": c.gates,
        "flags": c.flags,
        "sic_bucket": c.sic_bucket,
        "confidence": c.confidence,
        "scored": c.scored,
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
                    "momentum", "value", "opportunity", "insider", "upside_to_target",
                    "gates", "scored", "sic_bucket"])
        for i, c in enumerate(cards, 1):
            d = _card_dict(c)
            w.writerow([i, d["ticker"], d["composite"], d["quality"], d["moat"],
                        d["growth"], d["momentum"], d["value"], d["opportunity"],
                        d["insider"], d["upside_to_target"], "|".join(d["gates"]),
                        d["scored"], d["sic_bucket"]])


if __name__ == "__main__":
    raise SystemExit(main())
