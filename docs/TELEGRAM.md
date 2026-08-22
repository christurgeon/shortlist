# Telegram bot (`shortlist-bot`)

Drive the screener by chatting instead of maintaining a fixed watchlist. The bot long-polls
Telegram — **no webhook, no inbound ports** — and answers **only** your allowlisted
`TELEGRAM_CHAT_ID`; every other sender is silently ignored.

```bash
uv run shortlist-bot           # starts the long-poll loop (Ctrl-C to stop)
```

> **Run exactly one instance.** Two concurrent `getUpdates` pollers will 409 against each
> other.

## Setup

Both variables live in the repo-root `.env` (copy from `.env.example`) — never in
`config.yaml`, per the secrets house rule. The bot picks them up on restart.

```bash
TELEGRAM_BOT_TOKEN=123456789:AAE...   # from @BotFather: /newbot → HTTP API token
TELEGRAM_CHAT_ID=987654321            # your chat id (see below)
```

1. **Create the bot.** In Telegram, message [@BotFather](https://t.me/BotFather), send
   `/newbot`, follow the prompts, and copy the **HTTP API token** into `TELEGRAM_BOT_TOKEN`.
   Telegram's walkthrough:
   [botfather features](https://core.telegram.org/bots/features#botfather) ·
   [bot tutorial](https://core.telegram.org/bots/tutorial).
2. **Find your chat id.** Send any message to your new bot, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and read
   `result[].message.chat.id`. Alternatively, DM [@userinfobot](https://t.me/userinfobot),
   which replies with your id.

## Commands

| Command | Reply |
|---------|-------|
| `/screen nvda, lmt, msft` | Ranked dashboard (PNG chart + HTML deep-dive), in seconds. Comma- or space-separated, case-insensitive. |
| `/deep tsla` | Same, plus the Claude 10-K research brief (slower — opt-in). |
| `/portfolio` | Re-screens your tracked holdings: exposure weights, sector concentration, per-holding alerts. No arguments. |
| `/explain 13d` | Plain-English definition of a term, gate or flag appearing in these reports. |
| `/add NVDA 12` | Track a holding (shares optional; paste several: `/add NVDA, MSFT, LMT`). |
| `/thesis NVDA <why you own it>` | Record why you own a holding — the only command taking free-text prose. |
| `/hold NVDA <note>` | Log that you saw an alert and chose to keep the position. |
| `/remove NVDA <reason>` | Stop tracking a holding (non-destructive; alias `/sold`). |
| `/help` | Command list (alias `/start`). |

Malformed tickers are dropped **before any API spend**, with a note naming what was ignored.

### What `/deep` evidence looks like here

The HTML attachment carries the brief's grounding layer; the PNG deliberately does not, and
the chunked text fallback carries it only at `Detail.FULL`. Each finding shows its claim, with
the filing quote and the document that verified it behind a disclosure. Four states, and they
are not interchangeable — `research/report.py` is the reference wording, and the two surfaces
must keep saying the same thing:

| State | Shown as | Means |
|---|---|---|
| verified | nothing | the quote was located in one document shown to the model |
| `unverified` | a mark on the item | a quote was offered and could **not** be located — the fabrication signal |
| `no filing quote` | a mark on the item | the model declared its own inference. Legal in *sources of advantage* and *management findings* only |
| unknown | nothing | the brief predates verification, so nothing was ever checked — not a failure |

Only the middle two are marked: measured over the 17-brief corpus on 2026-08-22, 11.4% of
items (59 of 519) carry a mark, so marking every item would cost the distinction its meaning.
The two footer counts come straight from the record and are never merged — an unverified claim
means the model quoted something absent from the filing, and pooling it with declared
inferences destroys exactly that signal.

### Request caps

Soft per-request caps bound reply latency and API cost. Over the cap, the bot runs the first
N and tells you which tickers were not run — never a silent drop.

| Setting | Default | Bounds |
|---|---|---|
| `bot.max_screen` | 10 | Tickers accepted by one `/screen` |
| `bot.max_deep` | 3 | Tickers accepted by one `/deep` (each is a Claude call) |
| `bot.research_top_n` | 3 | Names sent to the research layer per `/deep` |
| `bot.research_phase_budget_s` | 2800 | Wall-clock ceiling for the whole research phase |
| `bot.poll_timeout_s` | 25 | `getUpdates` long-poll seconds |

The bot reuses the exact scorer and report pipeline as the CLI, and the HTTP cache makes warm
re-screens effectively free.

## Holdings and `/portfolio`

Positions live in a **bot-owned `positions.json` store** (gitignored, atomic writes —
`config.yaml: portfolio.store`), not a hand-edited CSV.

- Track with `/add NVDA 12` — shares optional, bulk `/add NVDA, MSFT, LMT`.
- Optionally record `/thesis NVDA <why you own it>`.
- Drop one with `/remove NVDA <reason>` — non-destructive, alias `/sold`.
- `/hold NVDA <note>` logs that you saw an alert and chose to keep the position.

`/hold` and `/remove` append to a **`decisions.jsonl` ledger** (`config.yaml:
portfolio.decisions`). `/remove` embeds the full position record first, so a removal is
recoverable.

`/portfolio` then screens your tracked names and replies with the usual report plus a
**Portfolio** section: position weights, sector concentration, and alerts on any holding that
trips a gate, fires a flag, isn't scored, or comes back as an unknown ticker. There is **no
brokerage sync and no cost basis**.

A portfolio larger than `portfolio.max_holdings` (default **50**) is screened up to the cap
with an explicit "alerts incomplete" warning naming the un-screened tickers.

> **Held-name 8-K alerting does not exist.** It was **removed**, not disabled: `de3c9f8`
> deleted `bot/monitor.py` and the whole `portfolio.monitor` config block, because the
> alerting needed a nightly scheduler to be an alert at all and its only producer
> (`scout/daily.py`) went with the scout. Re-adding it is an open decision that requires a
> scheduler, not a config flag. The original design — which describes a feature that is no
> longer built — is kept for reference in [`POSITION_MONITOR.md`](POSITION_MONITOR.md).

## Research kill-switch

To skip the Claude research phase behind `/deep` without redeploying:

```bash
touch research/STOP_RESEARCH     # file-based; persists across restarts
SHORTLIST_NO_RESEARCH=1 ...      # env var; one process
```

See [`RESEARCH.md`](RESEARCH.md) for the research layer itself.

## Deployment

An always-on systemd unit ships at
[`../deploy/shortlist-bot.service`](../deploy/shortlist-bot.service). Setup, the opt-in
`shortlist-accumulate.timer`, and the install script are documented in
[`../deploy/README.md`](../deploy/README.md).
