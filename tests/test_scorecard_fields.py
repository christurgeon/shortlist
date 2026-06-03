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


def test_risk_field_defaults_none_and_accepts_value():
    assert _card().risk is None
    assert _card(risk=42.0).risk == 42.0


def test_rank_key_orders_scored_then_composite_then_confidence():
    from shortlist.models import rank_key
    a = _card(composite=80.0, confidence=0.30, scored=True)
    b = _card(composite=78.0, confidence=1.0, scored=True)
    # composite dominates: the thin 80 still ranks ABOVE the complete 78 (no-bury).
    assert sorted([b, a], key=rank_key, reverse=True) == [a, b]
    # equal composite -> confidence breaks the tie (higher first)
    c_hi = _card(composite=80.0, confidence=0.90, scored=True)
    assert sorted([a, c_hi], key=rank_key, reverse=True) == [c_hi, a]
    # scored dominates composite
    not_scored = _card(composite=95.0, confidence=1.0, scored=False)
    scored = _card(composite=50.0, confidence=1.0, scored=True)
    assert sorted([not_scored, scored], key=rank_key, reverse=True) == [scored, not_scored]


def test_rank_key_works_on_duck_typed_card_without_confidence():
    from shortlist.models import rank_key
    class _Loose:
        composite = 70.0
        scored = True
    # no `confidence` attr -> getattr default 1.0, no AttributeError
    assert rank_key(_Loose()) == (True, 70.0, 1.0)


def test_thin_field_defaults_false():
    assert _card().thin is False
    assert _card(thin=True).thin is True


def test_thin_does_not_affect_rank_key_or_passed():
    from shortlist.models import rank_key
    a = _card(composite=70.0, confidence=0.30, scored=True, thin=True)
    b = _card(composite=70.0, confidence=0.30, scored=True, thin=False)
    assert rank_key(a) == rank_key(b)
    assert a.passed == b.passed


def test_stockmetrics_has_piotroski_fields_defaulting_none():
    from shortlist.models import StockMetrics
    m = StockMetrics(ticker="T")
    assert m.piotroski_f is None
    assert m.piotroski_f_legs is None


def test_scorecard_has_piotroski_field_defaulting_none():
    from shortlist.models import ScoreCard
    c = ScoreCard(ticker="T", composite=0.0, quality=None, moat=None, growth=None,
                  momentum=None, value=None, opportunity=None, insider=None)
    assert c.piotroski_f is None
    assert c.piotroski_f_legs is None
