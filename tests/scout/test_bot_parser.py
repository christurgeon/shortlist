from shortlist.scout.bot import parse_command, allowed_message


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


def test_parse_explain_recognized():
    assert parse_command("/explain 13d").name == "explain"
    assert parse_command("/explain").name == "explain"


def test_parse_explain_strips_botname():
    assert parse_command("/explain@MyBot 13d").name == "explain"


def test_explain_term_preserves_multiword_and_case():
    from shortlist.scout.bot import explain_term
    assert explain_term("/explain days to cover") == "days to cover"
    assert explain_term("/explain@MyBot SC 13-D") == "SC 13-D"
    assert explain_term("/explain") == ""
    assert explain_term("/explain   ") == ""
