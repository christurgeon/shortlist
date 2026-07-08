from shortlist.scout.bot import TelegramBot, Command


class FakeNotifier:
    def __init__(self):
        self.messages = []
        self.actions = []
        self.chat_id = "42"
    def configured(self): return True
    def send_message(self, text): self.messages.append(text); return True
    def send_chat_action(self, action="typing"): self.actions.append(action); return True


class FakeCard:
    # Faithful to the fields rank_key/_caption read AND the sub-score/metrics
    # fields no_data() reads. Defaults make a "present" (has-data) card; pass
    # empty=True for an unknown-symbol card (all sub-scores None, no market_cap).
    def __init__(self, ticker, composite=50.0, *, empty=False, flags=None):
        self.ticker = ticker
        self.composite = composite
        self.scored = True
        self.confidence = 1.0
        self.gates = []
        self.flags = flags or []
        if empty:
            self.quality = self.moat = self.growth = self.momentum = None
            self.value = self.insider = self.risk = None
            self.metrics = None
        else:
            self.quality = 50.0
            self.moat = self.growth = self.momentum = None
            self.value = self.insider = self.risk = None
            self.metrics = type("M", (), {"market_cap": 1e9})()


def _bot(**kw):
    cfg = {"scout": {"bot": {"max_screen": 2, "max_deep": 1},
                     "deep_screen_sources": ["mock"]}}
    return TelegramBot(FakeNotifier(), cfg, **kw)


def _caption_manifest():
    from types import SimpleNamespace
    from datetime import date
    return SimpleNamespace(session=date(2026, 6, 8), screened=2, raw=2)


def test_caption_surfaces_discretionary_flags_not_routine():
    from shortlist.scout.bot import _caption
    cards = [FakeCard("MRVL", 60, flags=["recent_8k", "social_hype"]),
             FakeCard("AAPL", 70, flags=[])]
    cap = _caption(_caption_manifest(), cards)
    assert "🏷️ MRVL: social_hype" in cap     # discretionary flag surfaced in caption
    assert "recent_8k" not in cap              # routine filing flag suppressed from caption
    assert "AAPL:" not in cap                  # a flag-less leader gets no flag line
    assert "Top:" in cap and "screened from" in cap   # base caption preserved


def test_caption_no_flags_no_badge():
    from shortlist.scout.bot import _caption
    cap = _caption(_caption_manifest(), [FakeCard("AAPL", 70)])
    assert "🏷️" not in cap


def test_screen_runs_harness_and_delivers_full_artifacts():
    calls = {}
    def screen_fn(tickers, sources, config, macro=None):
        calls["tickers"] = tickers; calls["sources"] = sources
        return [FakeCard("NVDA", 78.0), FakeCard("LMT", 61.0)]
    def report_fn(cards, manifest, *, assessments, macro=None, portfolio=None):
        calls["assessments"] = assessments; calls["signals"] = manifest.signals
        return type("A", (), {"png": b"PNG", "html": "<h>", "text": "txt"})()
    def deliver_fn(notifier, *, png, html, text, caption, session):
        calls.update(png=png, html=html, text=text, caption=caption, session=session)
        return None

    bot = _bot(screen_fn=screen_fn, report_fn=report_fn, deliver_fn=deliver_fn)
    bot._handle(Command("screen", ("NVDA", "LMT"), "/screen nvda lmt"))

    assert calls["tickers"] == ["NVDA", "LMT"]
    assert calls["sources"] == ["mock"]
    assert calls["assessments"] == {}
    assert calls["signals"] == []
    assert calls["png"] == b"PNG" and calls["html"] == "<h>" and calls["text"] == "txt"
    assert "NVDA" in calls["caption"] and "screened from 2 raw" in calls["caption"]
    assert calls["session"]
    assert "upload_photo" in bot.notifier.actions


def test_screen_soft_cap_truncates_and_warns():
    def screen_fn(tickers, sources, config, macro=None): return [FakeCard(t) for t in tickers]
    def report_fn(cards, manifest, *, assessments, macro=None, portfolio=None):
        return type("A", (), {"png": None, "html": "", "text": ""})()
    def deliver_fn(notifier, **kw): return None

    bot = _bot(screen_fn=screen_fn, report_fn=report_fn, deliver_fn=deliver_fn)
    bot._handle(Command("screen", ("A", "B", "C"), "/screen a b c"))   # max_screen=2
    assert any("first 2" in m and "1" in m for m in bot.notifier.messages)


def test_deep_researches_with_require_passed_false():
    seen = {}
    def screen_fn(tickers, sources, config, macro=None): return [FakeCard(t) for t in tickers]
    def research_fn(cards, config, scout_cfg, *, require_passed, top_n):
        seen["require_passed"] = require_passed; seen["top_n"] = top_n
        return ({}, {"TSLA": {"synthesis": "ok"}}, ["TSLA"], None, {})
    def report_fn(cards, manifest, *, assessments, macro=None, portfolio=None):
        seen["assessments"] = assessments
        return type("A", (), {"png": b"P", "html": "", "text": ""})()
    def deliver_fn(notifier, **kw): return None

    bot = _bot(screen_fn=screen_fn, research_fn=research_fn,
               report_fn=report_fn, deliver_fn=deliver_fn)
    bot._handle(Command("deep", ("TSLA",), "/deep tsla"))
    assert seen["require_passed"] is False
    assert seen["assessments"] == {"TSLA": {"synthesis": "ok"}}
    assert bot.notifier.actions or bot.notifier.messages   # acked the slow path


def test_help_and_unknown_reply_text():
    bot = _bot()
    bot._handle(Command("help", (), "/help"))
    bot._handle(Command("unknown", (), "/wat"))
    assert len(bot.notifier.messages) == 2


def test_screen_rejects_all_malformed_without_screening():
    called = {"screen": False}
    def screen_fn(tickers, sources, config, macro=None):
        called["screen"] = True; return []
    bot = _bot(screen_fn=screen_fn)
    bot._handle(Command("screen", ("HELLOWORLD", "123"), "/screen helloworld 123"))
    assert called["screen"] is False                 # quota provably saved
    assert not bot.notifier.actions                  # no "uploading photo" tease
    assert any("Invalid ticker format" in m and "HELLOWORLD" in m and "123" in m
               for m in bot.notifier.messages)       # names the offenders


def test_screen_empty_input_shows_usage_not_invalid():
    called = {"screen": False}
    def screen_fn(tickers, sources, config, macro=None):
        called["screen"] = True; return []
    bot = _bot(screen_fn=screen_fn)
    bot._handle(Command("screen", (), "/screen"))
    assert called["screen"] is False
    # No tokens at all → usage text, NOT the invalid-format reply.
    assert any("Usage: /screen" in m for m in bot.notifier.messages)
    assert not any("Invalid ticker format" in m for m in bot.notifier.messages)


def test_screen_filters_malformed_and_notes_them():
    seen = {}
    def screen_fn(tickers, sources, config, macro=None):
        seen["tickers"] = tickers
        return [FakeCard(t) for t in tickers]
    def report_fn(cards, manifest, *, assessments, macro=None, portfolio=None):
        return type("A", (), {"png": b"P", "html": "", "text": ""})()
    def deliver_fn(notifier, **kw): seen["delivered"] = True
    bot = _bot(screen_fn=screen_fn, report_fn=report_fn, deliver_fn=deliver_fn)
    bot._handle(Command("screen", ("NVDA", "HELLOWORLD"), "/screen nvda helloworld"))
    assert seen["tickers"] == ["NVDA"]              # only the well-formed one screened
    assert seen.get("delivered") is True
    assert any("Invalid ticker format" in m and "HELLOWORLD" in m
               for m in bot.notifier.messages)


def test_screen_some_no_data_delivers_present_and_notes_missing():
    seen = {}
    def screen_fn(tickers, sources, config, macro=None):
        return [FakeCard("NVDA"), FakeCard("ZZZZ", empty=True)]
    def report_fn(cards, manifest, *, assessments, macro=None, portfolio=None):
        seen["report_cards"] = [c.ticker for c in cards]
        seen["screened"] = manifest.screened; seen["raw"] = manifest.raw
        return type("A", (), {"png": b"P", "html": "", "text": ""})()
    def deliver_fn(notifier, *, png, html, text, caption, session):
        seen["delivered"] = True; seen["caption"] = caption
    bot = _bot(screen_fn=screen_fn, report_fn=report_fn, deliver_fn=deliver_fn)
    bot._handle(Command("screen", ("NVDA", "ZZZZ"), "/screen nvda zzzz"))
    assert seen["report_cards"] == ["NVDA"]          # junk row excluded from report
    assert seen["screened"] == 1 and seen["raw"] == 2  # honest manifest counts
    assert "1 screened from 2 raw" in seen["caption"]  # delivery reflects present-only
    assert any("No data for" in m and "ZZZZ" in m for m in bot.notifier.messages)


def test_screen_composed_notes_order():
    # All three trailing notes at once: malformed token (HELLOWORLD) + no-data card
    # (ZZZZ) + soft-cap overflow (AMD dropped at cap=3) + present (NVDA, LMT).
    # Expected message order: no-data note, then soft-cap note, then format note.
    def screen_fn(tickers, sources, config, macro=None):
        return [FakeCard(t, empty=(t == "ZZZZ")) for t in tickers]
    def report_fn(cards, manifest, *, assessments, macro=None, portfolio=None):
        return type("A", (), {"png": b"P", "html": "", "text": ""})()
    def deliver_fn(notifier, **kw): pass
    cfg = {"scout": {"bot": {"max_screen": 3, "max_deep": 1},
                     "deep_screen_sources": ["mock"]}}
    bot = TelegramBot(FakeNotifier(), cfg, screen_fn=screen_fn,
                      report_fn=report_fn, deliver_fn=deliver_fn)
    # Format filter drops HELLOWORLD -> good=[NVDA,LMT,ZZZZ,AMD]; cap=3 -> kept drops
    # AMD; screen returns NVDA(present), LMT(present), ZZZZ(no-data).
    bot._handle(Command("screen", ("NVDA", "LMT", "ZZZZ", "AMD", "HELLOWORLD"),
                        "/screen ..."))
    msgs = bot.notifier.messages
    nodata_i = next((i for i, m in enumerate(msgs) if "No data for" in m), -1)
    softcap_i = next((i for i, m in enumerate(msgs) if "more not run" in m), -1)
    fmt_i = next((i for i, m in enumerate(msgs) if "Invalid ticker format" in m), -1)
    assert 0 <= nodata_i < softcap_i < fmt_i


def test_screen_all_no_data_skips_report_entirely():
    seen = {"report": False, "deliver": False}
    def screen_fn(tickers, sources, config, macro=None):
        return [FakeCard("ZZZZ", empty=True)]
    def report_fn(cards, manifest, *, assessments, macro=None, portfolio=None):
        seen["report"] = True
        return type("A", (), {"png": b"P", "html": "", "text": ""})()
    def deliver_fn(notifier, **kw): seen["deliver"] = True
    bot = _bot(screen_fn=screen_fn, report_fn=report_fn, deliver_fn=deliver_fn)
    bot._handle(Command("screen", ("ZZZZ",), "/screen zzzz"))
    assert seen["report"] is False and seen["deliver"] is False
    assert any("No data for" in m for m in bot.notifier.messages)


def _deep_bot(max_deep, **fns):
    # Local fixture so each test can pick a cap; max_screen unused here.
    cfg = {"scout": {"bot": {"max_screen": 10, "max_deep": max_deep},
                     "deep_screen_sources": ["mock"]}}
    return TelegramBot(FakeNotifier(), cfg, **fns)


def test_deep_filters_malformed_and_researches_present_only():
    seen = {}
    def screen_fn(tickers, sources, config, macro=None):
        seen["tickers"] = tickers
        return [FakeCard(t) for t in tickers]
    def research_fn(cards, config, scout_cfg, *, require_passed, top_n):
        seen["research_cards"] = [c.ticker for c in cards]; seen["top_n"] = top_n
        return ({}, {}, [c.ticker for c in cards], None, {})
    def report_fn(cards, manifest, *, assessments, macro=None, portfolio=None):
        return type("A", (), {"png": b"P", "html": "", "text": ""})()
    def deliver_fn(notifier, **kw): pass
    # max_deep=2 so HELLOWORLD would survive the soft-cap if it weren't filtered —
    # this is what makes the test FAIL against current code (which screens it).
    bot = _deep_bot(2, screen_fn=screen_fn, research_fn=research_fn,
                    report_fn=report_fn, deliver_fn=deliver_fn)
    bot._handle(Command("deep", ("TSLA", "HELLOWORLD"), "/deep tsla helloworld"))
    assert seen["tickers"] == ["TSLA"]              # malformed dropped before screen
    assert seen["research_cards"] == ["TSLA"]
    assert seen["top_n"] == 1                       # len(present), not len(kept)
    assert any(m.startswith("Researching TSLA") for m in bot.notifier.messages)
    assert not any("HELLOWORLD" in m for m in bot.notifier.messages
                   if m.startswith("Researching"))


def test_deep_researching_message_names_capped_tickers():
    # Two well-formed names, cap=1: the "Researching…" pre-ack must name the
    # post-cap `kept` (AAPL), NOT the pre-cap `good` (AAPL, MSFT).
    def screen_fn(tickers, sources, config, macro=None): return [FakeCard(t) for t in tickers]
    def research_fn(cards, config, scout_cfg, *, require_passed, top_n):
        return ({}, {}, [c.ticker for c in cards], None, {})
    def report_fn(cards, manifest, *, assessments, macro=None, portfolio=None):
        return type("A", (), {"png": b"P", "html": "", "text": ""})()
    def deliver_fn(notifier, **kw): pass
    bot = _deep_bot(1, screen_fn=screen_fn, research_fn=research_fn,
                    report_fn=report_fn, deliver_fn=deliver_fn)
    bot._handle(Command("deep", ("AAPL", "MSFT"), "/deep aapl msft"))
    researching = [m for m in bot.notifier.messages if m.startswith("Researching")]
    assert researching and "AAPL" in researching[0] and "MSFT" not in researching[0]


def test_deep_sends_skip_reason_when_assessment_missing():
    # Present, data-rich card but research yields no assessment + a skip reason.
    # /deep must surface the reason LOUDLY rather than silently omitting analysis.
    def screen_fn(tickers, sources, config, macro=None):
        return [FakeCard("NVDA")]
    def research_fn(cards, config, scout_cfg, *, require_passed, top_n):
        return ({}, {}, [], None, {"NVDA": "assessment failed"})
    def report_fn(cards, manifest, *, assessments, macro=None, portfolio=None):
        return type("A", (), {"png": b"P", "html": "", "text": ""})()
    def deliver_fn(notifier, **kw): pass
    bot = _deep_bot(1, screen_fn=screen_fn, research_fn=research_fn,
                    report_fn=report_fn, deliver_fn=deliver_fn)
    bot._handle(Command("deep", ("NVDA",), "/deep nvda"))
    assert any("research unavailable" in m and "NVDA" in m and "assessment failed" in m
               for m in bot.notifier.messages)


def test_deep_all_no_data_skips_research_and_report():
    seen = {"research": False, "report": False, "deliver": False}
    def screen_fn(tickers, sources, config, macro=None):
        return [FakeCard("ZZZZ", empty=True)]
    def research_fn(cards, config, scout_cfg, *, require_passed, top_n):
        seen["research"] = True; return ({}, {}, [], None, {})
    def report_fn(cards, manifest, *, assessments, macro=None, portfolio=None):
        seen["report"] = True
        return type("A", (), {"png": b"P", "html": "", "text": ""})()
    def deliver_fn(notifier, **kw): seen["deliver"] = True
    bot = _deep_bot(1, screen_fn=screen_fn, research_fn=research_fn,
                    report_fn=report_fn, deliver_fn=deliver_fn)
    bot._handle(Command("deep", ("ZZZZ",), "/deep zzzz"))
    assert seen["research"] is False and seen["report"] is False
    assert seen["deliver"] is False                 # spec: never deliver on all-no-data
    assert any("No data for" in m for m in bot.notifier.messages)


# --- /portfolio tests ---

def _bot_pf(tmp_path, csv_text=None, **kw):
    cfg = {"scout": {"bot": {"max_screen": 2, "max_deep": 1},
                     "deep_screen_sources": ["mock"]},
           "portfolio": {"path": str(tmp_path / "portfolio.csv"), "max_holdings": 2}}
    if csv_text is not None:
        (tmp_path / "portfolio.csv").write_text(csv_text)
    return TelegramBot(FakeNotifier(), cfg, **kw)


def test_portfolio_missing_file_replies_with_setup_hint(tmp_path):
    bot = _bot_pf(tmp_path)                       # no file written
    bot._handle(Command("portfolio", (), "/portfolio"))
    assert any("portfolio.csv" in m for m in bot.notifier.messages)


def test_portfolio_happy_path_screens_and_delivers(tmp_path):
    calls = {}
    def screen_fn(tickers, sources, config, macro=None):
        calls["tickers"] = tickers
        return [FakeCard("AAPL", 70.0), FakeCard("LMT", 60.0)]
    def report_fn(cards, manifest, *, assessments, macro=None, portfolio=None):
        calls["portfolio"] = portfolio
        return type("A", (), {"png": b"P", "html": "<h>", "text": "t"})()
    def deliver_fn(notifier, *, png, html, text, caption, session):
        calls["delivered"] = True
        return None
    bot = _bot_pf(tmp_path, "AAPL,40\nLMT,15\n",
                  screen_fn=screen_fn, report_fn=report_fn, deliver_fn=deliver_fn)
    bot._handle(Command("portfolio", (), "/portfolio"))
    assert calls["tickers"] == ["AAPL", "LMT"]
    assert calls["portfolio"] is not None          # summary threaded
    assert hasattr(calls["portfolio"], "positions")   # a real PortfolioSummary, not a placeholder
    assert calls["delivered"] is True


def test_portfolio_over_cap_warns_incomplete(tmp_path):
    def screen_fn(tickers, sources, config, macro=None):
        return [FakeCard(t, 50.0) for t in tickers]
    def report_fn(cards, manifest, *, assessments, macro=None, portfolio=None):
        return type("A", (), {"png": b"P", "html": "<h>", "text": "t"})()
    def deliver_fn(notifier, *, png, html, text, caption, session):
        return None
    bot = _bot_pf(tmp_path, "AAPL,1\nLMT,1\nMSFT,1\n",   # 3 > max_holdings=2
                  screen_fn=screen_fn, report_fn=report_fn, deliver_fn=deliver_fn)
    bot._handle(Command("portfolio", (), "/portfolio"))
    warn = " ".join(bot.notifier.messages)
    assert "MSFT" in warn and "INCOMPLETE" in warn.upper()


def test_portfolio_all_no_data_still_delivers(tmp_path):
    # Monitoring intent: if every holding comes back no-data, still deliver a
    # portfolio report (holdings shown as no-data alerts) — NOT a dead-end.
    calls = {}
    def screen_fn(tickers, sources, config, macro=None):
        return [FakeCard(t, empty=True) for t in tickers]   # all no-data
    def report_fn(cards, manifest, *, assessments, macro=None, portfolio=None):
        calls["portfolio"] = portfolio
        return type("A", (), {"png": b"P", "html": "<h>", "text": "t"})()
    def deliver_fn(notifier, *, png, html, text, caption, session):
        calls["delivered"] = True
        return None
    bot = _bot_pf(tmp_path, "AAPL,1\nLMT,1\n",
                  screen_fn=screen_fn, report_fn=report_fn, deliver_fn=deliver_fn)
    bot._handle(Command("portfolio", (), "/portfolio"))
    assert calls["delivered"] is True
    assert calls["portfolio"] is not None
    assert "INCOMPLETE" not in " ".join(bot.notifier.messages)   # nothing dropped


def test_explain_no_arg_sends_index():
    bot = _bot()
    bot._handle(Command("explain", (), "/explain"))
    assert len(bot.notifier.messages) == 1
    assert "Known terms" in bot.notifier.messages[0]


def test_explain_known_term_sends_entry():
    bot = _bot()
    bot._handle(Command("explain", ("13D",), "/explain 13d"))
    assert bot.notifier.messages[0].startswith("13D")


def test_explain_unknown_term_suggests():
    bot = _bot()
    bot._handle(Command("explain", ("13X",), "/explain 13x"))
    msg = bot.notifier.messages[0]
    assert "No entry" in msg and "/explain" in msg


def test_help_mentions_explain():
    from shortlist.scout.bot import _HELP
    assert "/explain" in _HELP
