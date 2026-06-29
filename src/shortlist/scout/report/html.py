"""Zero-dep HTML assembly. Every interpolated value goes through HtmlBuilder.esc().

The visual design system lives entirely in `_CSS` below (a self-contained, inline
stylesheet so the document renders offline as a single file — it is delivered to
Telegram via sendDocument and opened cold on a phone). The data-driven heatmap
colors still come from `theme.score_to_rgb` via inline styles in `sections.py`;
this file owns the *chrome* palette (background, surfaces, type, accents).
"""
from __future__ import annotations

import html as _html
import re

_ISO_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


class HtmlBuilder:
    """Tiny tag helper with a single escaping choke-point. No templating engine."""

    def esc(self, s) -> str:
        return _html.escape("" if s is None else str(s), quote=True)

    def tag(self, name: str, text: str = "", **attrs) -> str:
        a = "".join(f' {k.lstrip("_").replace("_", "-")}="{self.esc(v)}"' for k, v in attrs.items())
        return f"<{name}{a}>{self.esc(text)}</{name}>"

    def raw(self, name: str, inner_html: str, **attrs) -> str:
        """Wrap already-safe inner HTML (built from other esc'd pieces)."""
        a = "".join(f' {k.lstrip("_").replace("_", "-")}="{self.esc(v)}"' for k, v in attrs.items())
        return f"<{name}{a}>{inner_html}</{name}>"


# A small inline radar/scope mark for brand character — pure SVG, no deps.
_LOGO = (
    '<svg class="mark" viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" '
    'fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round">'
    '<circle cx="12" cy="12" r="9" opacity=".35"/>'
    '<circle cx="12" cy="12" r="5" opacity=".55"/>'
    '<circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none"/>'
    '<path d="M12 12 L20 6" opacity=".9"/></svg>'
)


_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700;12..96,800&family=IBM+Plex+Mono:wght@400;500;600&family=Manrope:wght@400;500;600;700&display=swap');

:root{
  --bg:#090d18; --bg-2:#0c1120; --surface:#121a2e; --surface-2:#18223a;
  --line:#26334f; --line-soft:#1a2540;
  --text:#e9eef8; --text-2:#a3b0cc; --text-3:#8290af;
  --accent:#5ce7cd; --accent-2:#86a3ff;
  --bull:#5ad79c; --bear:#f5867f; --flag:#f4c074;
  --radius:18px; --r-sm:11px;
  --mono:'IBM Plex Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
  --disp:'Bricolage Grotesque','Manrope',system-ui,sans-serif;
  --body:'Manrope',-apple-system,'Segoe UI',Roboto,system-ui,sans-serif;
}

*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0; padding:0 14px 56px; font-family:var(--body); color:var(--text);
  font-size:15px; line-height:1.55; letter-spacing:.005em;
  background:
    radial-gradient(1100px 560px at 88% -8%, rgba(92,231,205,.11), transparent 60%),
    radial-gradient(900px 520px at -8% 2%, rgba(134,163,255,.11), transparent 55%),
    var(--bg);
  background-attachment:fixed;
}
.wrap{max-width:760px; margin:0 auto}
a{color:var(--accent); text-decoration:none}

/* ---- masthead ---- */
.masthead{
  position:sticky; top:0; z-index:10; margin:0 -14px 8px; padding:16px 18px 14px;
  background:linear-gradient(180deg, rgba(9,13,24,.92), rgba(9,13,24,.74) 70%, transparent);
  -webkit-backdrop-filter:blur(12px); backdrop-filter:blur(12px);
  border-bottom:1px solid var(--line-soft);
}
.brand{display:flex; align-items:center; gap:8px; color:var(--accent);
  font:700 12px/1 var(--mono); letter-spacing:.22em; text-transform:uppercase}
.brand .mark{filter:drop-shadow(0 0 8px rgba(92,231,205,.5))}
.masthead h1{
  font-family:var(--disp); font-weight:800; font-size:clamp(23px,7vw,34px);
  line-height:1.04; letter-spacing:-.02em; margin:9px 0 0;
  background:linear-gradient(180deg,#fff,#bcc8e6); -webkit-background-clip:text;
  background-clip:text; color:transparent;
}
.datechip{display:inline-block; margin-top:9px; padding:4px 11px; border-radius:999px;
  font:600 12px/1 var(--mono); letter-spacing:.04em; color:var(--accent);
  background:rgba(92,231,205,.10); border:1px solid rgba(92,231,205,.28)}

img.glance{max-width:100%; display:block; border-radius:var(--radius); margin:6px 0 4px;
  border:1px solid var(--line); box-shadow:0 18px 40px -22px rgba(0,0,0,.8)}

/* ---- sections ---- */
.sec{margin:26px 0 0; animation:rise .55s cubic-bezier(.2,.75,.25,1) both}
.sec:nth-of-type(1){animation-delay:.03s} .sec:nth-of-type(2){animation-delay:.10s}
.sec:nth-of-type(3){animation-delay:.17s} .sec:nth-of-type(4){animation-delay:.24s}
@keyframes rise{from{opacity:0; transform:translateY(14px)} to{opacity:1; transform:none}}
.sec-label{display:flex; align-items:center; gap:10px; margin:0 2px 12px;
  font:700 12px/1 var(--mono); letter-spacing:.2em; text-transform:uppercase; color:var(--text-3)}
.sec-label::after{content:""; flex:1; height:1px;
  background:linear-gradient(90deg,var(--line),transparent)}

/* ---- cards ---- */
.card{background:linear-gradient(180deg,var(--surface),var(--bg-2));
  border:1px solid var(--line); border-radius:var(--radius);
  padding:16px 17px; margin:13px 0; box-shadow:0 20px 44px -30px rgba(0,0,0,.85)}
.card h2{font-family:var(--disp); font-weight:700; font-size:17px; letter-spacing:-.01em;
  margin:0 0 12px; display:flex; align-items:baseline; gap:9px; flex-wrap:wrap}
.card h2 .tk{color:var(--accent)}
.card h2 .nm{color:var(--text-2); font-weight:600; font-size:14px; font-family:var(--body)}
.card h2 .sc{margin-left:auto; font:700 14px/1 var(--mono); color:var(--text)}

/* ---- leaderboard heatmap ---- */
.board-wrap{position:relative}
.board-wrap::after{content:""; position:absolute; top:1px; right:1px; bottom:1px; width:30px;
  border-radius:0 var(--radius) var(--radius) 0; pointer-events:none;
  background:linear-gradient(90deg, transparent, rgba(18,26,46,.92))}
.scroll-x{overflow-x:auto; -webkit-overflow-scrolling:touch; border-radius:var(--radius);
  border:1px solid var(--line); background:var(--surface);
  box-shadow:0 20px 44px -30px rgba(0,0,0,.85)}
table.board{border-collapse:separate; border-spacing:0; width:100%; min-width:max-content}
.board th,.board td{padding:9px 7px; white-space:nowrap; text-align:center}
.board thead th{font:700 10px/1 var(--mono); letter-spacing:.1em; text-transform:uppercase;
  color:var(--text-3); border-bottom:1px solid var(--line)}
.board tbody tr{border-top:1px solid var(--line-soft)}
.board tbody tr:nth-child(even) td{background:rgba(255,255,255,.012)}
.board .rk{color:var(--text-3); font:600 12px/1 var(--mono); width:1%}
.board .tik{position:sticky; left:0; z-index:3; text-align:left; padding-left:14px;
  background:var(--surface); box-shadow:6px 0 10px -8px rgba(0,0,0,.75)}
.board thead .tik{background:var(--surface)}
.board tbody tr:nth-child(even) .tik{background:#141d33}
.tik .t{font:700 14px/1 var(--disp); color:var(--text)}
.tik .n{display:block; font-size:10.5px; color:var(--text-3); margin-top:2px; max-width:120px;
  overflow:hidden; text-overflow:ellipsis}
.chip{display:inline-block; min-width:30px; padding:6px 8px; border-radius:8px;
  font:600 12.5px/1 var(--mono); text-align:center; letter-spacing:.01em}
.chip.comp{min-width:40px; padding:8px 9px; font-size:14px; font-weight:700; border-radius:9px;
  box-shadow:0 4px 14px -6px rgba(0,0,0,.6)}
.tags{text-align:left; padding-left:10px}
.tag{display:inline-block; font:600 10px/1.1 var(--mono); padding:4px 7px; border-radius:6px;
  margin:2px 4px 2px 0; letter-spacing:.02em; white-space:nowrap}
.tag-gate{background:rgba(245,134,127,.14); color:#ffb4af; border:1px solid rgba(245,134,127,.32)}
.tag-flag{background:rgba(244,192,116,.13); color:#f6d294; border:1px solid rgba(244,192,116,.30)}

/* ---- per-ticker flags strip (below the heatmap, outside its scroll) ---- */
.flags-strip{margin-top:10px; display:flex; flex-direction:column; gap:6px}
.flags-row{display:flex; flex-wrap:wrap; align-items:center; gap:2px}
.flags-row .fs-tik{font:700 11px/1.2 var(--mono); color:var(--text-2);
  min-width:52px; margin-right:6px}

/* ---- flag/gate glossary ---- */
.glossary{display:flex; flex-direction:column; gap:14px}
.gloss-group{display:flex; flex-direction:column; gap:6px}
.gloss-head{font:700 10px/1 var(--mono); letter-spacing:.1em; text-transform:uppercase;
  color:var(--text-3)}
.gloss-item{display:flex; flex-wrap:wrap; align-items:baseline; gap:8px}
.gloss-item .tag{flex:none}
.gloss-desc{color:var(--text-2); font-size:12.5px}

/* ---- fundamentals metric grid ---- */
.metrics{display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:1px;
  background:var(--line-soft); border:1px solid var(--line-soft);
  border-radius:var(--r-sm); overflow:hidden}
.metric{display:flex; justify-content:space-between; align-items:baseline; gap:10px;
  background:var(--bg-2); padding:9px 12px}
.metric .k{color:var(--text-2); font-size:12.5px}
.metric .v{font:600 12.5px/1 var(--mono); color:var(--text)}
.metric .v.pos{color:var(--bull)} .metric .v.neg{color:var(--bear)}
.metric .v.na{color:var(--text-3)}

/* ---- research prose ---- */
.call{display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin:2px 0 12px}
.pill{display:inline-flex; align-items:center; gap:6px; padding:5px 12px; border-radius:999px;
  font:700 12.5px/1 var(--body); letter-spacing:.01em; box-shadow:0 6px 16px -8px rgba(0,0,0,.7)}
.takeaway{font-family:var(--disp); font-weight:500; font-size:16px; line-height:1.5;
  color:var(--text); margin:4px 0 14px; padding-left:13px; border-left:3px solid var(--accent)}
.card p{margin:9px 0; color:var(--text)} .card p b{color:#fff; font-weight:700}
.callout{margin:11px 0; padding:11px 14px; border-radius:0 var(--r-sm) var(--r-sm) 0;
  background:var(--surface-2)}
.callout.bull{border-left:3px solid var(--bull)} .callout.bear{border-left:3px solid var(--bear)}
.callout.bull b{color:var(--bull)} .callout.bear b{color:var(--bear)}
.block{margin:12px 0} .block>b{display:block; font:700 11px/1 var(--mono); letter-spacing:.14em;
  text-transform:uppercase; color:var(--text-3); margin-bottom:7px}
.block ul{margin:0; padding:0; list-style:none}
.block li{position:relative; padding:4px 0 4px 18px; color:var(--text-2); font-size:14px}
.block li::before{content:""; position:absolute; left:3px; top:11px; width:5px; height:5px;
  border-radius:50%; background:var(--text-3)}
.block.flag li::before{background:var(--flag)}
.bull{color:var(--bull)} .bear{color:var(--bear)} .flag{color:var(--flag)}
.muted{color:var(--text-3); font-size:12.5px}

/* ---- footer / coverage ---- */
.cov{background:var(--surface); border:1px solid var(--line); border-radius:var(--radius);
  padding:15px 16px}
.sigs{display:flex; flex-wrap:wrap; gap:7px; margin-bottom:13px}
.sig{display:inline-flex; align-items:center; gap:6px; padding:5px 10px; border-radius:999px;
  font:600 11px/1 var(--mono); letter-spacing:.02em; border:1px solid var(--line)}
.sig.ok{color:var(--bull); background:rgba(90,215,156,.08); border-color:rgba(90,215,156,.28)}
.sig.no{color:var(--bear); background:rgba(245,134,127,.07); border-color:rgba(245,134,127,.26)}
.funnel{display:flex; flex-wrap:wrap; align-items:center; gap:6px 4px; margin-bottom:11px;
  font:500 12.5px/1.3 var(--mono); color:var(--text-2)}
.funnel b{color:var(--text); font-weight:600} .funnel .arw{color:var(--text-3)}
.funnel .drop{color:var(--flag)}
.note{display:flex; gap:8px; color:var(--text-3); font-size:12.5px; padding:4px 0}
.note::before{content:"›"; color:var(--accent); font-weight:700}
.macro{font:600 12.5px/1.5 var(--mono); color:var(--text-2); letter-spacing:.01em}
.deep{display:flex; flex-direction:column; gap:4px}
.deepcmd{font:600 13px/1.5 var(--mono); color:var(--text); background:var(--surface);
         border:1px solid var(--line); border-radius:var(--radius); padding:4px 8px}
.picks{display:flex; flex-direction:column; gap:3px}
.pick{font:13px/1.5 var(--mono); color:var(--text-2)}

@media (min-width:560px){ body{padding-left:22px; padding-right:22px} }
@media (prefers-reduced-motion:reduce){ *{animation:none !important} }
"""


def document(title: str, png_b64: str | None, body: str) -> str:
    b = HtmlBuilder()
    m = _ISO_DATE.search(title)
    headline = title[: m.start()].rstrip(" —-·") if m else title
    datechip = f'<div class="datechip">{b.esc(m.group(1))}</div>' if m else ""
    glance = (f'<img class="glance" src="data:image/png;base64,{png_b64}" '
              f'alt="dashboard glance">' if png_b64 else "")
    header = (f'<header class="masthead"><div class="brand">{_LOGO}'
              f'<span>Shortlist Scout</span></div>'
              f'<h1>{b.esc(headline)}</h1>{datechip}</header>')
    return (
        "<!DOCTYPE html>\n<html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
        "<meta name='color-scheme' content='dark'>"
        "<meta name='theme-color' content='#090d18'>"
        f"<title>{b.esc(title)}</title><style>{_CSS}</style></head>"
        f"<body><div class='wrap'>{header}{glance}{body}</div></body></html>"
    )
