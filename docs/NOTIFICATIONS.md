# Notifications — delivering the scout report

**Audience:** whoever operates the autonomous scout (`shortlist-scout`) and wants the
daily report to land somewhere a human will actually read it.
**Companion docs:** [`AUTONOMOUS_SCOUT.md`](AUTONOMOUS_SCOUT.md) (the loop that produces the
report — see §3 step 8 and §6) and the repo `CLAUDE.md` (the secrets / redaction house rules
this extends).

> **Status:** §1–§3 describe **shipped** behaviour. §3 was a plan (2026-06-03); it is now
> **implemented** (2026-06). See spec `docs/superpowers/specs/2026-06-04-scout-reporting-notifications-design.md`
> and plan `docs/superpowers/plans/2026-06-04-scout-reporting-notifications.md`.
>
> **Update (2026-06-07):** delivery now has an **inbound** counterpart — the interactive bot
> (`scout/bot.py`, CLI `shortlist-bot`) long-polls Telegram `getUpdates` so the operator can
> request screens on demand. It reuses this doc's `TelegramNotifier` transport for *sending*
> and adds the inbound methods (`get_updates` / `delete_webhook` / `send_chat_action`). The
> autonomous daily push described in §1–§3 is now opt-in (`scout.daily_push.enabled`, off by
> default). See §7.

---

## 1. The goal and the honest framing

The scout's last step is delivery. A daily run produces a `ScoreCard` shortlist + briefs +
the signal-coverage line, rendered by `report.py:render_message` into one plain-text message.
That message has to reach the operator reliably, and a **delivery failure must be loud, not
silent** — the same coverage-honesty rule the rest of the pipeline follows.

Two design decisions, settled:

| Decision | Choice | Consequence |
|---|---|---|
| **Primary transport** | **Telegram** (reuses the oracle-bot token pattern) | Report lands on the operator's phone. |
| **Always-on fallback** | **File artifact + stdout/journal** | The report is recoverable even when Telegram is unconfigured or down. |

The framing to keep honest: **Telegram is best-effort; the file artifact is the source of
truth.** A configured-but-failed send is recorded, written to disk, and exits non-zero so the
systemd `OnFailure` hook fires — it is *not* retried into a duplicate report.

---

## 2. What ships today

### 2.1 The transport (`scout/notify.py`)

`send_telegram(text, token=None, chat_id=None, client=None) -> bool` — a thin POST to
`api.telegram.org/bot<token>/sendMessage`. Credentials come from `TELEGRAM_BOT_TOKEN` /
`TELEGRAM_CHAT_ID` (env or repo-root `.env`). Returns `False` when unconfigured or on any
error; error strings route through `env.redact_secrets()` before printing (the token is in the
URL). Injectable `client` keeps it testable without network.

### 2.2 Delivery semantics (`scout/daily.py` step 4)

The orchestrator, not the transport, owns the policy:

- **Unconfigured Telegram** (no token/chat) → print to stdout/journal, write the artifact,
  mark the run complete, **exit 0**. This is the expected default mode on a box without a bot.
- **Configured but the send failed** → print to stdout, write the artifact, append
  `"telegram delivery failed (configured)"` to the manifest, mark the run complete (so it is
  *not* retried), **exit 2** so `OnFailure` surfaces it.
- **Delivered** → still write the artifact (trend debugging), exit 0.

### 2.3 The always-on artifact (`scout/<date>/`)

`_write_manifest` writes the rendered message (`report.txt`), the styled HTML deep-dive
(`report.html`), the PNG dashboard glance (`dashboard.png`), and the full `RunManifest`
(`manifest.json` — signal availability, funnel counts, budget drops, research outcome) under
`scout/<date>/` (gitignored) **on every run, regardless of Telegram outcome**. This is the
recoverable source of truth and the input to any future trend analysis.

### 2.4 The gaps (why §3 exists)

1. **No message chunking.** Telegram caps `sendMessage` at **4096 chars**. A full 15-name
   report with briefs can exceed that → the API rejects it (`400`) and the whole report is
   lost to Telegram (file artifact still survives). Today this is unhandled.
2. **No retry.** A transient `429`/`5xx`/network blip drops the send for the day. The oracle
   bot and the scout's own FMP client both already do `Retry-After`-aware backoff; delivery
   does not.
3. **No formatting.** Plain text only — no `parse_mode`, so the ranked list isn't emphasised
   and URLs aren't tidy. Acceptable, but a readability miss.
4. **Single transport, hard-wired.** `daily.py` imports `send_telegram` directly. Adding a
   second channel (email, a webhook, ntfy) means editing the orchestrator.

---

## 3. Hardening — IMPLEMENTED (2026-06)

The transport is `scout/notify.py:TelegramNotifier` + `deliver()`. Delivery is a PNG chart
(sendPhoto) + HTML deep-dive document (sendDocument), with a chunked plain-text fallback when
Telegram is unconfigured or failing. Spec:
`docs/superpowers/specs/2026-06-04-scout-reporting-notifications-design.md`; plan:
`docs/superpowers/plans/2026-06-04-scout-reporting-notifications.md`.

The original plan enumerated four additions; all are shipped. Summary below for reference:

### 3.1 Chunking

Split the rendered message into ≤4096-char parts on **paragraph then line** boundaries
(never mid-line; never mid-name). Send parts in order; number them (`(1/3)`) so a reader knows
the report is multi-part. A failure on part *k* aborts the rest and reports
`"delivered 2/3 parts"` — partial honesty over a false success.

```python
def chunk(text: str, limit: int = 4096) -> list[str]: ...   # pure, unit-tested
```

`chunk` is pure (no I/O) so the boundary logic is tested in isolation, exactly like
`edgar_index.cluster_buys_from_records`.

### 3.2 Retry with backoff

Wrap the POST in the same `Retry-After`-aware backoff the FMP client uses (`fmp.max_retries`
is the precedent). Retry only `429`/`5xx`/transport errors; never retry a `400` (malformed —
retrying won't help) or `401/403` (bad token — fail fast and loud). Cap total wait so a hung
Telegram can't blow the systemd run's wall-clock.

### 3.3 Formatting (optional, config-gated)

Send with `parse_mode=MarkdownV2` when `notify.markdown: true`. **MarkdownV2 requires escaping**
`_*[]()~`>#+-=|{}.!` in all non-markup text — an unescaped char is a `400`, which is exactly
the failure §3.2 must *not* retry. Default **off** (plain text) until the escaper is fixture-
tested against a real report; a formatting bug must never cost the day's delivery.

### 3.4 A transport seam

Introduce a tiny `Notifier` protocol so `daily.py` depends on an interface, not `send_telegram`:

```python
class Notifier(Protocol):
    def send(self, text: str) -> DeliveryResult: ...   # (ok: bool, detail: str, parts: int)
    def configured(self) -> bool: ...
```

`TelegramNotifier` is the only implementation now; `StdoutNotifier` formalises the
unconfigured fallback. `build_notifier(config)` mirrors the existing `build_signals` /
`_REGISTRY` pattern. This makes the §2.2 policy testable without monkeypatching and leaves
room for a second channel as a one-file addition — **no orchestrator edit**.

`DeliveryResult.detail` feeds the manifest (`"delivered 3/3 parts"` /
`"failed: 429 after 3 retries"`), so the artifact records *why* a delivery degraded — the
same audit trail the signal-coverage line gives discovery.

### 3.5 Config (new `notify:` block in `config.yaml`)

```yaml
notify:
  transport: telegram        # telegram | stdout ; future: email, webhook
  markdown: false            # MarkdownV2 (§3.3) — off until the escaper is fixture-tested
  max_retries: 3             # 429/5xx backoff (§3.2)
  chunk_limit: 4096          # Telegram hard cap (§3.1)
```

Credentials stay in `.env` (never config) per the secrets house rule. An empty/missing
`notify:` block reproduces today's behaviour bit-for-bit (telegram if keyed, else stdout).

---

## 4. Testing

- **`chunk`**: a >4096-char report splits on line boundaries, never mid-name; `(k/n)` markers
  correct; a sub-limit message stays one part.
- **`TelegramNotifier`**: recorded `200` → `ok`; `400` → no retry, fails loud; `429` with
  `Retry-After` → retries then succeeds/fails per fixture; redaction asserted on the error path.
- **Delivery policy** (`daily.py`): unconfigured → exit 0 + artifact; configured-failed →
  exit 2 + artifact + manifest note; delivered → exit 0 + artifact. (Extends the existing
  `test_fixes.py` / `test_notify.py` coverage.)
- **MarkdownV2 escaper** (if §3.3 lands): every reserved char escaped; round-trips a real
  recorded report without a `400`.

House rules carried through (from `CLAUDE.md`): every error string that may carry the bot
token routes through `env.redact_secrets()`; the file artifact is written on every path so a
delivery failure is always recoverable.

---

## 5. Scope boundaries (YAGNI)

**In scope (plan):** the hardened `TelegramNotifier` (chunking, backoff, optional markdown),
the `Notifier` seam + `StdoutNotifier`, the `notify:` config block, and tests.

**Out of scope (tracked, not built):**
- Additional transports (email, generic webhook, ntfy) — the seam makes each a one-file
  addition; build on demand, not speculatively.
- Inbound/interactive Telegram (commands, buttons) — the scout is a one-way daily push.
- Per-name push threading or images — the plain ranked list is the product.
- Rate-limit coordination across multiple daily runs — the scout is one-shot/day; not needed.

---

## 6. Operator notes

- **Enable Telegram:** add `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` to the repo-root `.env`.
  The scout auto-detects them; no redeploy needed. The file artifact is still written.
- **Where the report goes today (no Telegram configured):** the systemd journal and
  `scout/<date>/{report.txt,report.html,dashboard.png,manifest.json}`. Journal command depends on how the unit was installed —
  `journalctl -u shortlist-scout.service` for the shipped **system** unit
  (`deploy/shortlist-scout.service`, `User=oracle`), or `journalctl --user -u
  shortlist-scout.service` if installed as a **user** unit.
- **Failure alerting:** a configured-but-failed send exits non-zero — point the service's
  `OnFailure=` at an alert unit (see `deploy/README.md`).

---

## 7. Inbound — the interactive bot (`scout/bot.py`)

§1–§6 cover **outbound** delivery of the autonomous daily report. The interactive bot adds the
**inbound** half: the operator drives screening by chatting, instead of waiting for the (now
opt-in) daily push. Design spec: `docs/superpowers/specs/2026-06-06-scout-telegram-bot-design.md`.

- **Transport reuse.** Sending goes through the same `TelegramNotifier` (§2.1) — `send_photo` /
  `send_document` / `send_message`, all `redact_secrets`-guarded. The bot adds three inbound
  methods on the same class: `get_updates(offset, timeout, client) -> PollResult` (long-poll),
  `delete_webhook(drop_pending_updates=True)`, and `send_chat_action`.
- **Long-poll, not webhook.** `shortlist-bot` calls `getUpdates` on a loop (no inbound ports /
  HTTPS). On boot it `delete_webhook(drop_pending_updates=True)` + an `offset=-1` probe to
  discard any backlog, so a restart never replays stale commands. A **single worker thread**
  runs the network-heavy handlers (`run_harness` → `build_report` → `deliver`), so a slow
  `/deep` never stalls polling. The loop never dies: malformed updates are skipped, handler
  errors are caught and replied as a `redact_secrets`-filtered message, transport errors back
  off, and a `409 Conflict` (a second poller) alerts once then backs off.
- **Commands.** `/screen <tickers>` → the same PNG + HTML report this doc describes;
  `/deep <ticker>` → adds the Claude brief; `/help`. Soft per-request caps live in
  `config.yaml: scout.bot` (`max_screen` / `max_deep` / `poll_timeout_s`).
- **Allowlist.** The bot answers **only** `TELEGRAM_CHAT_ID` (private text messages); every other
  sender / chat type / edited message is silently ignored — no reply, so it isn't an oracle for
  token-guessers. There is no quota guard by design: only the operator can reach the bot, and
  gating/429s degrade honestly via the existing coverage diagnostic.
- **Coexistence.** Polling and sending share one bot token without conflict — only **two
  concurrent `getUpdates` pollers** trigger a 409, so run exactly one bot instance. The daily
  push (`sendMessage`/`sendPhoto`) never polls, so it coexists with the bot on the same token.
- **Shutdown.** SIGTERM sets a stop flag; the in-flight long-poll returns within one poll cycle
  (the blocked read isn't interrupted), so graceful shutdown takes ≈40s — the unit's
  `TimeoutStopSec=50` is sized to exceed it. Always-on unit: `deploy/shortlist-bot.service`
  (`Type=simple`).
