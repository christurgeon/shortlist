from shortlist.bot.report import theme


def test_stance_rgb_and_emoji():
    assert theme.stance_to_rgb("STRONG_BUY") == (26, 152, 80)
    assert theme.stance_to_rgb("STRONG_AVOID") == (215, 48, 39)
    assert theme.stance_to_rgb("HOLD") == (255, 235, 130)
    assert theme.stance_to_rgb("nonsense") == theme.GRAY_BAD
    assert theme.stance_emoji("BUY") == "🟢"
    assert theme.stance_emoji("AVOID") == "🔴"
    assert theme.stance_emoji("HOLD") == "🟡"
    assert theme.stance_emoji("nonsense") == ""


def test_stance_keys_match_canonical_stances():
    from shortlist.research.models import STANCES
    from shortlist.bot.report import theme
    assert set(theme.STANCE_RGB) == set(STANCES)
    assert set(theme._STANCE_EMOJI) == set(STANCES)
    assert theme.stance_to_rgb("BUY") == (102, 189, 99)
    assert theme.stance_to_rgb("AVOID") == (244, 109, 67)
