from shortlist.data.sources import ret_between, _yh_ret_over


def test_ret_between_matches_yh_ret_over_for_trailing():
    xs = [10.0 * (1.0 + i * 0.01) for i in range(300)]  # gently rising
    # ret_between(xs, 127, 1) is the trailing 6m return == _yh_ret_over(xs, 126)
    assert ret_between(xs, 127, 1) == _yh_ret_over(xs, 126)


def test_ret_between_skip_window():
    xs = [1.0] * 300
    xs[-22] = 2.0      # numerator price
    xs[-274] = 1.0     # denominator price
    # 12-1: closes[-22]/closes[-274] - 1
    assert ret_between(xs, 274, 22) == 1.0


def test_ret_between_none_on_short_history():
    assert ret_between([1.0] * 100, 274, 22) is None


def test_ret_between_none_on_zero_denominator():
    xs = [1.0] * 300
    xs[-274] = 0.0
    assert ret_between(xs, 274, 22) is None


def test_ret_between_none_on_bad_end_back():
    assert ret_between([1.0] * 300, 274, 0) is None
