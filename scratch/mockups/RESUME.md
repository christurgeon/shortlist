# Scout notifications — brainstorm WIP (resume here)

Parked from a brainstorming session on 2026-06-04. Branch: `worktree-scout-notifications`.

## Goal
Give the scout (`shortlist-scout`) a real Telegram client + richer daily report,
borrowing the **working** oracle client and adding charts. Brainstorm was paused
before the design was finalized ("plan it later").

## What we decided / learned
- **Port from `/opt/oracle/python/notifications/`** (the client we know works):
  - `base.py` — clean `Notifier` Protocol + `AlertLevel` enum → **borrow as-is**.
  - `telegram_bot.py` (1063 lines) — a long-running **PTB command daemon** coupled to
    trading subsystems. The scout is a **one-shot push**, so borrow only the
    send-hardening (rate-limit, `_safe_send`, redaction); **drop** the
    `Application`/CommandHandler daemon + command machinery.
  - `formatters.py` — text only; **oracle has NO chart/image code** → charts are net-new.
- **Telegram can't render HTML/charts inline.** Realistic delivery: native text
  (`parse_mode`), inline image via **`sendPhoto`** (recommended), attached HTML file,
  or a hosted dashboard link.
- Existing design doc already on `main`: **`docs/NOTIFICATIONS.md`** (delivery
  semantics §2 + a hardening plan §3: chunking for the 4096 cap, retry/backoff,
  `Notifier` seam). The port plan should fold into / supersede that.

## Mockups in this dir (rendered from a REAL screen: NVDA/MSFT/GOOGL/AAPL/LMT/JPM)
- `mockup_a_heatmap.png` — ranked sub-score heatmap (recommended primary view).
- `mockup_b_radar.png` — radar small-multiples for the top 3 (per-leader add-on).
- `mockup_c_dashboard.png` — composite bars + upside + heatmap.
- `render.py` — regenerate: `uv run --with matplotlib --with numpy python scratch/mockups/render.py`
- `cards.json` — the real ScoreCard data the mockups use.
- JPM's gray heatmap cells = genuine sector-masked legs (bank) — abstention honesty.

## Open decision (where we stopped)
Pick the primary daily format (heatmap image A / dashboard C / radar B / text-only),
then write the spec via the brainstorming → writing-plans flow.
