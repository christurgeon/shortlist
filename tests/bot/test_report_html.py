from shortlist.bot.report.html import HtmlBuilder, document


def test_esc_escapes_all_dangerous_chars():
    h = HtmlBuilder()
    out = h.esc('<script>"x" & y</script>')
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert "&amp;" in out and "&quot;" in out


def test_tag_escapes_text_content_and_attrs():
    h = HtmlBuilder()
    assert h.tag("td", "A & B") == "<td>A &amp; B</td>"
    assert h.tag("td", "x", style="color:red") == '<td style="color:red">x</td>'
    assert "&quot;" in h.tag("td", "x", title='a"b')   # attr value escaped


def test_document_is_self_contained_html():
    out = document("Shortlist — 2026-06-04", png_b64=None, body="<p>hi</p>")
    assert out.startswith("<!DOCTYPE html>")
    assert "<style>" in out and "Shortlist — 2026-06-04" in out and "<p>hi</p>" in out


def test_document_embeds_png_when_present():
    out = document("T", png_b64="AAAA", body="")
    assert 'src="data:image/png;base64,AAAA"' in out


def test_underscore_prefixed_class_attr_becomes_class():
    h = HtmlBuilder()
    out = h.tag("td", "x", _class="k")
    assert 'class="k"' in out and "-class" not in out


def test_raw_also_maps_class_attr():
    h = HtmlBuilder()
    out = h.raw("div", "<p>hi</p>", _class="card")
    assert 'class="card"' in out and "-class" not in out
