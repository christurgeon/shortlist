from shortlist.bot.telegram import TelegramBot
from shortlist.bot.notify import PollResult


class FakeNotifier:
    def __init__(self, chat_id="42"):
        self.chat_id = chat_id
        self.messages = []
        self.actions = []
        self.deleted_webhook = False
        self.token = "T"
    def configured(self): return True
    def send_message(self, text): self.messages.append(text); return True
    def send_chat_action(self, action="typing"): self.actions.append(action); return True
    def delete_webhook(self, drop_pending_updates=True):
        self.deleted_webhook = True; return True


def _upd(uid, text, chat_id=42):
    return {"update_id": uid, "message": {"text": text,
            "chat": {"id": chat_id, "type": "private"}}}


def test_loop_discards_backlog_then_dispatches_and_stops():
    notifier = FakeNotifier()
    bot = TelegramBot(notifier, {"bot": {"poll_timeout_s": 0}})

    calls = {"n": 0}
    def fake_get_updates(offset, timeout, client):
        calls["n"] += 1
        if calls["n"] == 1:
            return PollResult(200, [_upd(100, "/old")])     # backlog probe
        if calls["n"] == 2:
            return PollResult(200, [_upd(101, "/help")])    # live
        bot._stop.set()
        return PollResult(200, [])
    notifier.get_updates = fake_get_updates

    bot.run()

    assert notifier.deleted_webhook is True
    assert bot._offset == 102
    assert any(m.startswith("Shortlist bot") for m in notifier.messages)
    assert not any("Unknown command" in m for m in notifier.messages)


def test_loop_ignores_foreign_chat_no_send():
    notifier = FakeNotifier()
    bot = TelegramBot(notifier, {"bot": {"poll_timeout_s": 0}})
    calls = {"n": 0}
    def fake_get_updates(offset, timeout, client):
        calls["n"] += 1
        if calls["n"] == 1:
            return PollResult(200, [])
        if calls["n"] == 2:
            return PollResult(200, [_upd(50, "/screen x", chat_id=99)])  # NOT 42
        bot._stop.set()
        return PollResult(200, [])
    notifier.get_updates = fake_get_updates
    bot.run()
    assert notifier.messages == [] and notifier.actions == []
    assert bot._queue.empty() and bot._offset == 51


def test_loop_survives_malformed_update():
    notifier = FakeNotifier()
    bot = TelegramBot(notifier, {"bot": {"poll_timeout_s": 0}})
    calls = {"n": 0}
    def fake_get_updates(offset, timeout, client):
        calls["n"] += 1
        if calls["n"] == 1:
            return PollResult(200, [])
        if calls["n"] == 2:
            return PollResult(200, [{"garbage": True}, _upd(10, "/help")])  # malformed + good
        bot._stop.set()
        return PollResult(200, [])
    notifier.get_updates = fake_get_updates
    bot.run()   # must not raise; the good /help still gets handled
    assert any(m.startswith("Shortlist bot") for m in notifier.messages)


def test_loop_409_alerts_once_and_backs_off():
    notifier = FakeNotifier()
    bot = TelegramBot(notifier, {"bot": {"poll_timeout_s": 0}})
    calls = {"n": 0}
    def fake_get_updates(offset, timeout, client):
        calls["n"] += 1
        if calls["n"] == 1:
            return PollResult(200, [])          # backlog probe
        if calls["n"] <= 3:
            return PollResult(409, [])
        bot._stop.set()
        return PollResult(200, [])
    notifier.get_updates = fake_get_updates
    bot._stop.wait = lambda *_a, **_k: None     # backoff sleeps -> instant
    bot.run()
    conflict_msgs = [m for m in notifier.messages if "another poller" in m]
    assert len(conflict_msgs) == 1


def test_client_read_timeout_exceeds_poll_timeout():
    notifier = FakeNotifier()
    bot = TelegramBot(notifier, {"bot": {"poll_timeout_s": 25}})
    def fake_get_updates(offset, timeout, client):
        bot._stop.set()
        return PollResult(200, [])
    notifier.get_updates = fake_get_updates
    bot.run()
    assert bot._client.timeout.read > bot.poll_timeout


def test_worker_survives_handler_exception_and_replies_redacted():
    notifier = FakeNotifier()
    def boom_screen(tickers, sources, config, macro=None):
        raise RuntimeError("https://api.telegram.org/botSECRET123/getUpdates failed")
    bot = TelegramBot(notifier, {"bot": {}}, screen_fn=boom_screen,
                      report_fn=lambda *a, **k: None, deliver_fn=lambda *a, **k: None)
    from shortlist.bot.telegram import Command
    bot._handle_safely(Command("screen", ("X",), "/screen x"))
    assert any("command failed" in m for m in notifier.messages)
    assert not any("SECRET123" in m for m in notifier.messages)   # token redacted


def test_main_returns_1_when_unconfigured(monkeypatch, tmp_path):
    from shortlist.bot import telegram as bot
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    # CRITICAL on the real VPS: oracle-prod's .env has live TELEGRAM_* creds, and
    # main() calls load_env() which would repopulate them process-wide AFTER the
    # delenv — making the bot "configured" and the test hang/poll. Stub load_env so
    # main() cannot reload creds from a real .env.
    monkeypatch.setattr(bot, "load_env", lambda: None)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("scout: {bot: {}}\n")
    # Unconfigured notifier -> run() returns 1 without polling.
    assert bot.main(["--config", str(cfg)]) == 1
