from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from .env import load_env, redact_secrets
from .merge import merge
from .models import ScoreCard
from .providers import build_providers
from .scoring import score


def run(tickers: list[str], provider_names: list[str], config: dict) -> list[ScoreCard]:
    providers = []
    for name in provider_names:
        try:
            providers.extend(build_providers([name]))
        except Exception as e:  # missing key or uninstalled SDK -> skip the source
            print(f"  ! skipping provider '{name}': {redact_secrets(e)}", file=sys.stderr)
    if not providers:
        print("No usable providers. Set API keys or use --demo.", file=sys.stderr)
        return []
    cards: list[ScoreCard] = []
    for t in tickers:
        per_provider = []
        for p in providers:
            try:
                per_provider.append(p.fetch(t))
            except Exception as e:  # one bad source shouldn't kill the run
                print(f"  ! {p.name} failed for {t}: {redact_secrets(e)}", file=sys.stderr)
        if not per_provider:
            continue
        cards.append(score(merge(per_provider), config))
    cards.sort(key=lambda c: c.composite, reverse=True)
    return cards


def _print_table(cards: list[ScoreCard]) -> None:
    try:
        from rich.console import Console
        from rich.table import Table
    except ImportError:
        _print_plain(cards)
        return

    table = Table(title="Moat + opportunity screen", title_style="bold")
    for col in ("Rank", "Ticker", "Composite", "Quality", "Moat",
                "Momentum", "Value", "Insider", "Upside", "Flags"):
        table.add_column(col, justify="right" if col != "Ticker" else "left")
    for i, c in enumerate(cards, 1):
        up = c.metrics.upside_to_target() if c.metrics else None
        style = "dim red" if c.gates else None
        table.add_row(
            str(i), c.ticker, f"{c.composite:.1f}",
            _f(c.quality), _f(c.moat), _f(c.momentum), _f(c.value), _f(c.insider),
            f"{up*100:.0f}%" if up is not None else "-",
            ",".join(c.gates) or "-", style=style,
        )
    Console().print(table)


def _print_plain(cards: list[ScoreCard]) -> None:
    print(f"{'#':>2} {'TICK':<6} {'COMP':>5} {'QUAL':>5} {'MOAT':>5} "
          f"{'MOM':>5} {'VAL':>5} {'INSD':>5}  FLAGS")
    for i, c in enumerate(cards, 1):
        print(f"{i:>2} {c.ticker:<6} {c.composite:>5} {_f(c.quality):>5} "
              f"{_f(c.moat):>5} {_f(c.momentum):>5} {_f(c.value):>5} "
              f"{_f(c.insider):>5}  {','.join(c.gates) or '-'}")


def _f(x):
    return f"{x:.0f}" if x is not None else "-"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="shortlist")
    ap.add_argument("--tickers", help="comma-separated, e.g. GEV,LMT,SCHW,TMO,GOOGL")
    ap.add_argument("--provider", help="comma-separated provider chain; overrides config")
    ap.add_argument("--config", default=str(Path(__file__).parent.parent.parent / "config.yaml"))
    ap.add_argument("--demo", action="store_true", help="offline run on the sample basket")
    ap.add_argument("--csv", help="write ranked results to this CSV path")
    ap.add_argument("--json", action="store_true", help="emit JSON to stdout instead of a table")
    args = ap.parse_args(argv)

    load_env()  # pick up API keys from a .env file if present

    config = yaml.safe_load(Path(args.config).read_text())

    if args.demo:
        tickers = ["GEV", "LMT", "SCHW", "TMO", "GOOGL"]
        providers = ["mock"]
    else:
        if not args.tickers:
            ap.error("--tickers is required unless --demo")
        tickers = [t.strip().upper() for t in args.tickers.split(",")]
        providers = (args.provider.split(",") if args.provider
                     else config.get("providers", ["fmp"]))

    cards = run(tickers, providers, config)

    if args.csv:
        _write_csv(cards, args.csv)
        print(f"wrote {args.csv}")
    if args.json:
        print(json.dumps([_card_dict(c) for c in cards], indent=2))
    else:
        _print_table(cards)
    return 0


def _card_dict(c: ScoreCard) -> dict:
    up = c.metrics.upside_to_target() if c.metrics else None
    return {
        "ticker": c.ticker, "composite": c.composite, "quality": c.quality,
        "moat": c.moat, "momentum": c.momentum, "value": c.value,
        "opportunity": c.opportunity, "insider": c.insider,
        "upside_to_target": round(up, 3) if up is not None else None,
        "gates": c.gates,
    }


def _write_csv(cards: list[ScoreCard], path: str) -> None:
    import csv

    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "ticker", "composite", "quality", "moat", "momentum",
                    "value", "opportunity", "insider", "upside_to_target", "gates"])
        for i, c in enumerate(cards, 1):
            d = _card_dict(c)
            w.writerow([i, d["ticker"], d["composite"], d["quality"], d["moat"],
                        d["momentum"], d["value"], d["opportunity"], d["insider"],
                        d["upside_to_target"], "|".join(d["gates"])])


if __name__ == "__main__":
    raise SystemExit(main())
