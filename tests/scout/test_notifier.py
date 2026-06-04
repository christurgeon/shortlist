import httpx
from shortlist.scout.notify import TelegramNotifier, deliver, DeliveryResult


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


def test_configured_reflects_credentials():
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
