from shortlist.scout.report.theme import score_to_rgb, SUBS, SUB_LABELS, GRAY_BAD


def test_colormap_endpoints_and_midpoint():
    assert score_to_rgb(0)[0] > score_to_rgb(0)[1]      # red: R dominates
    assert score_to_rgb(100)[1] > score_to_rgb(100)[0]  # green: G dominates
    mid = score_to_rgb(50)
    assert mid[0] > 150 and mid[1] > 150                 # yellow: high R and G
    assert mid[2] < mid[0] and mid[2] < mid[1]           # ...and low B (not gray/white)


def test_colormap_none_is_gray_and_clamps():
    assert score_to_rgb(None) == GRAY_BAD
    assert score_to_rgb(-20) == score_to_rgb(0)          # clamp low
    assert score_to_rgb(140) == score_to_rgb(100)        # clamp high


def test_subscore_order_and_labels_aligned():
    assert SUBS == ["quality", "moat", "growth", "value", "momentum", "insider", "risk"]
    assert [SUB_LABELS[s] for s in SUBS] == ["Qual", "Moat", "Grow", "Value", "Mom", "Insdr", "Risk"]
