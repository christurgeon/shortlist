"""Pillow raster of the curated glance (composite bars + sub-score heatmap). The ONLY
module that imports Pillow. Scales to any N; empty N renders an honest card."""
from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

from .theme import BG, FG, SUB_LABELS, SUBS, score_to_rgb, stance_to_rgb, text_on
from .viewmodel import ReportVM

_W = 760           # target width (px); we supersample 2x then downscale
_ROW = 34          # px per row (per panel)
_CHROME = 150      # fixed px overhead (title + axis + padding)
_SS = 2            # supersample factor

_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _font(size, bold=False):
    path = _FONT_PATHS[1] if bold else _FONT_PATHS[0]
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        try:
            return ImageFont.load_default(size=size)   # Pillow >= 10
        except TypeError:
            return ImageFont.load_default()


def _center(d, box, text, font, fill):
    bl, bt, br, bb = d.textbbox((0, 0), text, font=font)
    x = box[0] + (box[2] - box[0] - (br - bl)) / 2 - bl
    y = box[1] + (box[3] - box[1] - (bb - bt)) / 2 - bt
    d.text((x, y), text, font=font, fill=fill)


def render_glance(vm: ReportVM) -> bytes:
    n = len(vm.leaders)
    s = _SS
    if n == 0:
        img = Image.new("RGB", (_W * s, 120 * s), BG)
        d = ImageDraw.Draw(img)
        _center(d, (0, 0, _W * s, 120 * s),
                f"No candidates passed screening — {vm.session.isoformat()}",
                _font(15 * s, bold=True), FG)
        return _finish(img)

    height = _CHROME + 2 * _ROW * n
    img = Image.new("RGB", (_W * s, height * s), BG)
    d = ImageDraw.Draw(img)
    f_title = _font(15 * s, bold=True)
    f_lbl = _font(11 * s, bold=True)
    f_cell = _font(10 * s, bold=True)

    pad = 14 * s
    d.text((pad, 10 * s), f"Scout daily dashboard — {vm.session.isoformat()}",
           font=f_title, fill=FG)

    left = 70 * s                 # gutter for ticker labels
    plot_w = _W * s - left - pad

    # --- composite bars panel ---
    y0 = 44 * s
    d.text((pad, y0 - 18 * s), "Composite", font=f_lbl, fill=FG)
    for i, ld in enumerate(vm.leaders):
        ry = y0 + i * _ROW * s
        d.text((pad, ry + 6 * s), ld.ticker, font=f_lbl, fill=FG)
        bw = int(plot_w * max(0.0, min(100.0, ld.composite)) / 100.0)
        col = score_to_rgb(ld.composite)
        d.rectangle([left, ry + 3 * s, left + bw, ry + (_ROW - 6) * s], fill=col)
        d.text((left + bw + 6 * s, ry + 6 * s), f"{ld.composite:.0f}", font=f_cell, fill=FG)
        a = ld.assessment
        if a is not None and a.call_stance:
            pcol = stance_to_rgb(a.call_stance)
            label = a.call_label or a.call_stance
            bb = d.textbbox((0, 0), label, font=f_cell)
            tw = bb[2] - bb[0]
            px2 = _W * s - pad
            px1 = px2 - (tw + 14 * s)
            d.rounded_rectangle([px1, ry + 4 * s, px2, ry + (_ROW - 7) * s],
                                 radius=5 * s, fill=pcol)
            _center(d, (px1, ry + 4 * s, px2, ry + (_ROW - 7) * s), label, f_cell,
                    text_on(pcol))

    # --- sub-score heatmap panel ---
    hy = y0 + n * _ROW * s + 24 * s
    d.text((pad, hy - 18 * s), "Sub-scores", font=f_lbl, fill=FG)
    cols = len(SUBS)
    cw = plot_w / cols
    for j, sub in enumerate(SUBS):
        cx = left + j * cw
        _center(d, (cx, hy - 16 * s, cx + cw, hy), SUB_LABELS[sub], f_cell, FG)
    for i, ld in enumerate(vm.leaders):
        ry = hy + i * _ROW * s
        d.text((pad, ry + 8 * s), ld.ticker, font=f_lbl, fill=FG)
        for j, sub in enumerate(SUBS):
            v = ld.subscores.get(sub)
            cx = left + j * cw
            box = (cx + 1, ry + 1, cx + cw - 1, ry + _ROW * s - 1)
            col = score_to_rgb(v)
            d.rectangle(list(box), fill=col)
            _center(d, box, "·" if v is None else f"{v:.0f}", f_cell, text_on(col))

    return _finish(img)


def _finish(img: Image.Image) -> bytes:
    w, h = img.size
    img = img.resize((w // _SS, h // _SS), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
