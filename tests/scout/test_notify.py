import httpx
from shortlist.scout.notify import send_telegram


def test_send_posts_to_bot_api_and_returns_true():
    seen = {}
    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"ok": True})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    ok = send_telegram("hello", token="T", chat_id="42", client=client)
    assert ok is True
    assert "/botT/sendMessage" in seen["url"]
    assert "42" in seen["body"] and "hello" in seen["body"]


def test_send_without_creds_returns_false(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert send_telegram("x", token=None, chat_id=None) is False
