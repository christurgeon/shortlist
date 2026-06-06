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
    # Faithful to the fields rank_key (+ _caption's sort) actually read on a ScoreCard.
    def __init__(self, ticker, composite=50.0):
        self.ticker = ticker
        self.composite = composite
        self.scored = True
        self.confidence = 1.0
        self.gates = []


def _bot(**kw):
    cfg = {"scout": {"bot": {"max_screen": 2, "max_deep": 1},
                     "deep_screen_sources": ["mock"]}}
    return TelegramBot(FakeNotifier(), cfg, **kw)


def test_screen_runs_harness_and_delivers_full_artifacts():
    calls = {}
    def screen_fn(tickers, sources, config):
        calls["tickers"] = tickers; calls["sources"] = sources
        return [FakeCard("NVDA", 78.0), FakeCard("LMT", 61.0)]
    def report_fn(cards, manifest, *, assessments):
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
    def screen_fn(tickers, sources, config): return [FakeCard(t) for t in tickers]
    def report_fn(cards, manifest, *, assessments):
        return type("A", (), {"png": None, "html": "", "text": ""})()
    def deliver_fn(notifier, **kw): return None

    bot = _bot(screen_fn=screen_fn, report_fn=report_fn, deliver_fn=deliver_fn)
    bot._handle(Command("screen", ("A", "B", "C"), "/screen a b c"))   # max_screen=2
    assert any("first 2" in m and "1" in m for m in bot.notifier.messages)


def test_deep_researches_with_require_passed_false():
    seen = {}
    def screen_fn(tickers, sources, config): return [FakeCard(t) for t in tickers]
    def research_fn(cards, config, scout_cfg, *, require_passed, top_n):
        seen["require_passed"] = require_passed; seen["top_n"] = top_n
        return ({}, {"TSLA": {"synthesis": "ok"}}, ["TSLA"], None)
    def report_fn(cards, manifest, *, assessments):
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
