from shortlist.scout.bot import parse_command, allowed_message, Command


def test_parse_screen_splits_comma_and_space_uppercases_dedupes():
    c = parse_command("/screen nvda, lmt msft NVDA")
    assert c.name == "screen"
    assert c.tickers == ("NVDA", "LMT", "MSFT")   # order preserved, deduped


def test_parse_strips_botname_suffix_and_is_case_insensitive():
    assert parse_command("/Screen@MyScoutBot AAPL").name == "screen"
    assert parse_command("/DEEP tsla").name == "deep"


def test_parse_help_start_and_unknown():
    assert parse_command("/help").name == "help"
    assert parse_command("/start").name == "start"
    assert parse_command("/wat foo").name == "unknown"
    assert parse_command("not a command").name == "unknown"


def test_parse_no_args_yields_empty_tickers():
    c = parse_command("/screen")
    assert c.name == "screen" and c.tickers == ()


def test_allowed_message_accepts_private_text_from_operator():
    upd = {"update_id": 5, "message": {"text": "/screen aapl",
           "chat": {"id": 42, "type": "private"}}}
    assert allowed_message(upd, "42") == "/screen aapl"


def test_allowed_message_rejects_foreign_chat():
    upd = {"message": {"text": "/screen aapl", "chat": {"id": 99, "type": "private"}}}
    assert allowed_message(upd, "42") is None


def test_allowed_message_rejects_group_and_nontext_and_edited():
    assert allowed_message({"message": {"text": "/x", "chat": {"id": 42, "type": "group"}}}, "42") is None
    assert allowed_message({"message": {"chat": {"id": 42, "type": "private"}}}, "42") is None  # no text
    assert allowed_message({"edited_message": {"text": "/x", "chat": {"id": 42, "type": "private"}}}, "42") is None
    assert allowed_message({"message": {"text": "/x", "chat": {"id": 42, "type": "private"}}}, None) is None
