from shortlist.bot.report.sections import Detail, _ValidationScoreboard


def _verdict(**over):
    base = {
        "signal": "edgar:activist_13d", "verdict": "INSUFFICIENT", "ir": None,
        "alpha_monthly": None, "alpha_ci": None, "effective_blocks": 3,
        "n_selected": 5, "n_measurable": 3, "measurable_fraction": 0.6,
        "sensitivity_flip": False, "cohort_type": "raw", "notes": [],
        "double_sort": None, "n_immature": 0, "n_events": 5,
    }
    base.update(over)
    return base


def _vm(validation):
    return type("VM", (), {"validation": validation})()


def test_absent_when_none():
    assert _ValidationScoreboard().applies(_vm(None)) is False


def test_absent_when_not_a_dict():
    # pre-digest-wiring shape was a bare list; the new section requires the envelope dict.
    assert _ValidationScoreboard().applies(_vm([_verdict()])) is False


def test_absent_when_verdicts_missing():
    assert _ValidationScoreboard().applies(_vm({"as_of": "2026-07-01", "source": "live"})) is False


def test_absent_when_verdicts_empty():
    data = {"as_of": "2026-07-01", "source": "live", "verdicts": []}
    assert _ValidationScoreboard().applies(_vm(data)) is False


def test_absent_when_verdicts_not_a_list():
    data = {"as_of": "2026-07-01", "source": "live", "verdicts": "oops"}
    assert _ValidationScoreboard().applies(_vm(data)) is False


def test_applies_true_on_happy_envelope():
    data = {"as_of": "2026-07-01", "source": "live", "verdicts": [_verdict()]}
    assert _ValidationScoreboard().applies(_vm(data)) is True


def test_render_text_returns_list_of_str():
    data = {"as_of": "2026-07-01", "source": "live", "verdicts": [_verdict()]}
    lines = _ValidationScoreboard().render_text(_vm(data), Detail.FULL)
    assert isinstance(lines, list)
    assert all(isinstance(ln, str) for ln in lines)


def test_render_text_absent_returns_empty_list():
    assert _ValidationScoreboard().render_text(_vm(None), Detail.FULL) == []
    empty = {"as_of": "2026-07-01", "source": "live", "verdicts": []}
    assert _ValidationScoreboard().render_text(_vm(empty), Detail.FULL) == []


def test_render_text_has_exact_disclaimer_label():
    data = {"as_of": "2026-07-01", "source": "live", "verdicts": [_verdict()]}
    lines = _ValidationScoreboard().render_text(_vm(data), Detail.FULL)
    assert "display / provisional / survivorship-biased — not evidence, not advice." in lines


def test_render_text_never_says_promote():
    data = {"as_of": "2026-07-01", "source": "live", "verdicts": [_verdict(verdict="KILL")]}
    lines = _ValidationScoreboard().render_text(_vm(data), Detail.FULL)
    assert "PROMOTE" not in "\n".join(lines).upper()


def test_render_text_includes_as_of_and_source():
    data = {"as_of": "2026-07-01", "source": "backfill:events.jsonl", "verdicts": [_verdict()]}
    joined = "\n".join(_ValidationScoreboard().render_text(_vm(data), Detail.FULL))
    assert "2026-07-01" in joined and "backfill:events.jsonl" in joined


def test_render_text_per_verdict_fields():
    v = _verdict(signal="finra:short_interest", verdict="KILL", cohort_type="scored_gated",
                ir=-0.42, effective_blocks=8, n_selected=50, n_measurable=42)
    data = {"as_of": "2026-07-01", "source": "live", "verdicts": [v]}
    joined = "\n".join(_ValidationScoreboard().render_text(_vm(data), Detail.FULL))
    assert "finra:short_interest" in joined
    assert "scored_gated" in joined
    assert "KILL" in joined
    assert "-0.42" in joined
    assert "blocks=8" in joined
    assert "n=42/50" in joined


def test_render_text_ir_ci_from_alpha_ci_list():
    # asdict() turns SignalVerdict.alpha_ci (a tuple) into a JSON list on the round-trip.
    v = _verdict(ir=-0.10, alpha_ci=[-0.30, 0.05])
    data = {"as_of": "2026-07-01", "source": "live", "verdicts": [v]}
    joined = "\n".join(_ValidationScoreboard().render_text(_vm(data), Detail.FULL))
    assert "-0.30" in joined and "0.05" in joined


def test_render_text_synthetic_marker():
    v = _verdict(notes=["SYNTHETIC backfill cohort — rank/KILL only (M1)"])
    data = {"as_of": "2026-07-01", "source": "backfill:x.jsonl", "verdicts": [v]}
    joined = "\n".join(_ValidationScoreboard().render_text(_vm(data), Detail.FULL))
    assert "SYNTHETIC" in joined


def test_render_text_no_synthetic_marker_when_absent():
    v = _verdict(notes=["some other note"])
    data = {"as_of": "2026-07-01", "source": "live", "verdicts": [v]}
    joined = "\n".join(_ValidationScoreboard().render_text(_vm(data), Detail.FULL))
    assert "SYNTHETIC" not in joined


def test_render_text_double_sort_line():
    ds = {"n_high": 20, "n_low": 22, "months": 30, "effective_blocks": 6,
         "spread_alpha_monthly": 0.0123, "spread_ci": [-0.01, 0.03],
         "high_ir": 0.5, "low_ir": -0.2}
    v = _verdict(double_sort=ds)
    data = {"as_of": "2026-07-01", "source": "live", "verdicts": [v]}
    joined = "\n".join(_ValidationScoreboard().render_text(_vm(data), Detail.FULL))
    assert "double-sort" in joined
    assert "0.0123" in joined
    assert "-0.01" in joined and "0.03" in joined
    assert "n=20/22" in joined


def test_render_text_no_double_sort_line_when_none():
    v = _verdict(double_sort=None)
    data = {"as_of": "2026-07-01", "source": "live", "verdicts": [v]}
    joined = "\n".join(_ValidationScoreboard().render_text(_vm(data), Detail.FULL))
    assert "double-sort" not in joined


# --- Task 2 (B2/I4): the immature count must stay legible on a young live cohort --------

def test_render_text_shows_immature_count_when_present():
    v = _verdict(n_selected=0, n_measurable=0, measurable_fraction=0.0,
                 n_immature=350, n_events=350)
    data = {"as_of": "2026-07-01", "source": "live", "verdicts": [v]}
    joined = "\n".join(_ValidationScoreboard().render_text(_vm(data), Detail.FULL))
    assert "n=0/0 (+350 immature)" in joined


def test_render_text_omits_immature_suffix_when_zero():
    v = _verdict(n_immature=0)
    data = {"as_of": "2026-07-01", "source": "live", "verdicts": [v]}
    joined = "\n".join(_ValidationScoreboard().render_text(_vm(data), Detail.FULL))
    assert "immature)" not in joined


def test_render_html_shows_immature_count_when_present():
    from shortlist.bot.report.html import HtmlBuilder
    v = _verdict(n_selected=0, n_measurable=0, n_immature=350, n_events=350)
    data = {"as_of": "2026-07-01", "source": "live", "verdicts": [v]}
    html = _ValidationScoreboard().render_html(_vm(data), HtmlBuilder())
    assert "(+350 immature)" in html


def test_render_html_has_disclaimer_and_verdict_signal():
    from shortlist.bot.report.html import HtmlBuilder
    data = {"as_of": "2026-07-01", "source": "live",
           "verdicts": [_verdict(signal="edgar:activist_13d")]}
    html = _ValidationScoreboard().render_html(_vm(data), HtmlBuilder())
    assert "survivorship-biased" in html
    assert "edgar:activist_13d" in html
    assert "PROMOTE" not in html.upper()


def test_render_text_marks_a_suppressed_level():
    # The digest never renders notes, so without an explicit marker a floor-suppressed level
    # is indistinguishable from one that simply could not be computed.
    v = _verdict(alpha_suppressed=True, measurable_fraction=0.62)
    data = {"as_of": "2026-07-01", "source": "live", "verdicts": [v]}
    joined = "\n".join(_ValidationScoreboard().render_text(_vm(data), Detail.FULL))
    assert "level suppressed" in joined


def test_render_text_no_suppressed_marker_when_absent():
    # Old persisted verdicts predate the field entirely -> must render exactly as before.
    v = _verdict()
    v.pop("alpha_suppressed", None)
    data = {"as_of": "2026-07-01", "source": "live", "verdicts": [v]}
    joined = "\n".join(_ValidationScoreboard().render_text(_vm(data), Detail.FULL))
    assert "suppressed" not in joined
