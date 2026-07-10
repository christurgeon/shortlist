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


def test_retries_on_transient_5xx_then_succeeds(monkeypatch):
    slept = []
    monkeypatch.setattr("shortlist.scout.notify.time.sleep", lambda s: slept.append(s))
    seq = [503, 200]

    def handler(request):
        return httpx.Response(seq.pop(0), json={"ok": True})

    n = TelegramNotifier("T", "42", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert n.send_message("hi") is True       # retried past the 503 to the 200
    assert len(slept) == 1                     # backed off once (no Retry-After -> default)


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


def test_send_message_chunks_by_utf16_units_for_astral_emoji():
    """Telegram's 4096 cap counts UTF-16 code units; astral-plane emoji weigh 2. A
    code-point chunker would pack 4096 emoji (8192 UTF-16 units) into one chunk -> 400."""
    import json as _json
    from shortlist.scout.notify import _MSG_CAP
    bodies = []
    n = TelegramNotifier("T", "42", client=_body_client(bodies))
    text = "\N{ROCKET}" * 3000                      # 3000 code points = 6000 UTF-16 units
    assert n.send_message(text) is True
    assert len(bodies) == 2                          # 2048 + 952 emoji, not one 3000-chunk
    total = ""
    for b in bodies:
        chunk = _json.loads(b.decode())["text"]
        assert len(chunk.encode("utf-16-le")) // 2 <= _MSG_CAP
        total += chunk
    assert total == text                             # content preserved, no split surrogate


def test_send_message_ascii_chunking_unchanged():
    # For ASCII (1 UTF-16 unit per char) the chunk boundaries are byte-identical to the
    # old code-point split: 4096 + 4096 + 808.
    import json as _json
    bodies = []
    n = TelegramNotifier("T", "42", client=_body_client(bodies))
    assert n.send_message("x" * 9000) is True
    assert [len(_json.loads(b.decode())["text"]) for b in bodies] == [4096, 4096, 808]


def test_non_200_logs_one_stderr_line_with_redacted_body(capsys):
    def handler(request):
        return httpx.Response(400, json={"ok": False,
                                         "description": "Bad Request: see ?token=SECRET"})
    n = TelegramNotifier("T", "42",
                         client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert n.send_message("hi") is False
    err = capsys.readouterr().err
    assert "telegram sendMessage failed: HTTP 400" in err
    assert "SECRET" not in err                       # redact_secrets applied to the body
    assert len(err.strip().splitlines()) == 1        # one line, not per-retry spam


def test_send_message_reuses_one_client_across_chunks(monkeypatch):
    """A multi-chunk message must not open a fresh httpx.Client per chunk."""
    import shortlist.scout.notify as notify
    created = []
    real_client = httpx.Client

    def counting_client(*a, **k):
        created.append(1)
        k["transport"] = httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True}))
        return real_client(*a, **k)

    monkeypatch.setattr(notify.httpx, "Client", counting_client)
    n = TelegramNotifier("T", "42")                  # no injected client
    assert n.send_message("x" * 9000) is True        # 3 chunks
    assert len(created) == 1                         # one client for the whole message
