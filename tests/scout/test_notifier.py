import httpx
from shortlist.scout.notify import TelegramNotifier, deliver


def _client(seen, status=200):
    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(status, json={"ok": True})
    return httpx.Client(transport=httpx.MockTransport(handler))


def _body_client(bodies):
    def handler(request):
        bodies.append(request.read())
        return httpx.Response(200, json={"ok": True})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_configured_reflects_credentials(monkeypatch):
    # Isolate from ambient env: another test's load_env() may have loaded a real .env's
    # TELEGRAM_* creds into os.environ for the session (load_dotenv persists process-wide).
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert TelegramNotifier("T", "42").configured() is True
    assert TelegramNotifier(None, None).configured() is False


def test_send_message_chunks_and_preserves_content():
    bodies = []
    n = TelegramNotifier("T", "42", client=_body_client(bodies))
    assert n.send_message("x" * 9000) is True
    assert len(bodies) == 3                      # 4096 + 4096 + 808
    # Each JSON body contains the key `"text"` which itself has one 'x'.
    # 3 chunks × 1 extra 'x' from the key = 9003 total in the raw body bytes.
    joined = "".join(b.decode() for b in bodies)
    assert joined.count("x") == 9003             # 9000 payload + 1 per chunk from "text" key


def test_send_message_empty_sends_nothing_but_succeeds():
    seen = []
    n = TelegramNotifier("T", "42", client=_client(seen))
    assert n.send_message("") is True and seen == []


def test_send_photo_and_document_hit_correct_endpoints():
    seen = []
    n = TelegramNotifier("T", "42", client=_client(seen))
    assert n.send_photo(b"\x89PNG", "cap") is True
    assert n.send_document(b"<html>", "r.html", "cap") is True
    assert any("/sendPhoto" in u for u in seen) and any("/sendDocument" in u for u in seen)


def test_caption_truncated_to_1024():
    bodies = []
    n = TelegramNotifier("T", "42", client=_body_client(bodies))
    n.send_photo(b"x", "y" * 5000)
    assert b"y" * 1025 not in bodies[0]           # caption capped


class _Fake:
    def __init__(self, configured=True, photo=True, doc=True, msg=True):
        self._c, self.photo, self.doc, self.msg = configured, photo, doc, msg
        self.calls = []
    def configured(self): return self._c
    def send_photo(self, png, cap): self.calls.append("photo"); return self.photo
    def send_document(self, data, fn, cap): self.calls.append("doc"); return self.doc
    def send_message(self, text): self.calls.append("msg"); return self.msg


def test_deliver_sequences_photo_then_document():
    f = _Fake()
    res = deliver(f, png=b"x", html="<h>", text="t", caption="c", session="2026-06-04")
    assert f.calls == ["photo", "doc"] and res.configured and res.all_ok


def test_deliver_doc_failure_falls_back_to_message():
    f = _Fake(doc=False)
    res = deliver(f, png=b"x", html="<h>", text="t", caption="c", session="x")
    assert "msg" in f.calls and not res.all_ok and "document" in " ".join(res.failures)


def test_deliver_photo_failure_still_sends_doc_and_message():
    f = _Fake(photo=False)
    res = deliver(f, png=b"x", html="<h>", text="t", caption="c", session="x")
    assert f.calls == ["photo", "doc", "msg"] and "photo" in res.failures


def test_deliver_unconfigured_does_nothing():
    f = _Fake(configured=False)
    res = deliver(f, png=None, html="<h>", text="t", caption="c", session="x")
    assert f.calls == [] and not res.configured and not res.all_ok


def test_retries_on_429_then_succeeds(monkeypatch):
    slept = []
    monkeypatch.setattr("shortlist.scout.notify.time.sleep", lambda s: slept.append(s))
    seq = [429, 200]

    def handler(request):
        return httpx.Response(seq.pop(0), headers={"Retry-After": "1"}, json={"ok": True})

    n = TelegramNotifier("T", "42", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert n.send_message("hi") is True       # retried past the 429 to the 200
    assert slept == [1.0]                      # honored Retry-After once


def test_429_exhausts_retries_and_returns_false(monkeypatch):
    monkeypatch.setattr("shortlist.scout.notify.time.sleep", lambda s: None)

    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "1"}, json={"ok": False})

    n = TelegramNotifier("T", "42", max_retries=2,
                         client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert n.send_message("hi") is False       # all attempts 429 -> False, no infinite loop


def test_deliver_skips_document_when_html_none():
    f = _Fake()
    res = deliver(f, png=b"x", html=None, text="t", caption="c", session="x")
    assert f.calls == ["photo"] and res.all_ok        # no doc, no fallback msg


def test_deliver_sends_message_when_nothing_attached():
    f = _Fake()
    res = deliver(f, png=None, html=None, text="t", caption="c", session="x")
    assert f.calls == ["msg"] and res.all_ok


def _json_client(status, payload):
    def handler(request):
        return httpx.Response(status, json=payload)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_get_updates_returns_results_on_200():
    from shortlist.scout.notify import TelegramNotifier
    client = _json_client(200, {"ok": True, "result": [{"update_id": 7}]})
    n = TelegramNotifier("T", "42")
    res = n.get_updates(offset=0, timeout=0, client=client)
    assert res.status == 200 and res.updates == [{"update_id": 7}]


def test_get_updates_409_reports_status_no_updates():
    from shortlist.scout.notify import TelegramNotifier
    client = _json_client(409, {"ok": False})
    res = TelegramNotifier("T", "42").get_updates(offset=0, timeout=0, client=client)
    assert res.status == 409 and res.updates == []


def test_get_updates_transport_error_status_zero():
    from shortlist.scout.notify import TelegramNotifier
    def handler(request):
        raise httpx.ConnectError("boom")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    res = TelegramNotifier("T", "42").get_updates(offset=0, timeout=0, client=client)
    assert res.status == 0 and res.updates == []


def test_delete_webhook_drops_pending_and_chat_action_carries_chat_id():
    seen, bodies = [], []
    def handler(request):
        seen.append(str(request.url)); bodies.append(request.read())
        return httpx.Response(200, json={"ok": True})
    n = TelegramNotifier("T", "42", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert n.delete_webhook() is True               # default drop_pending_updates=True
    assert n.send_chat_action("typing") is True
    assert any("deleteWebhook" in u for u in seen)
    assert any("sendChatAction" in u for u in seen)
    joined = "".join(b.decode() for b in bodies)
    assert "drop_pending_updates" in joined          # backlog cleared server-side
    assert "typing" in joined and "42" in joined      # chat_action carries chat_id (required)
