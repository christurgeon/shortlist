from shortlist.models import ScoreCard


def _card(**kw):
    base = dict(ticker="X", composite=50.0, quality=None, moat=None, growth=None,
                momentum=None, value=None, opportunity=None, insider=None)
    base.update(kw)
    return ScoreCard(**base)


def test_new_fields_default_backcompat():
    c = _card()
    assert c.sic_bucket is None
    assert c.confidence == 1.0
    assert c.scored is True
    assert c.abstentions == []


def test_passed_requires_scored_and_no_gates():
    assert _card().passed is True
    assert _card(gates=["over_leveraged"]).passed is False
    assert _card(scored=False).passed is False
    assert _card(gates=["x"], scored=False).passed is False
