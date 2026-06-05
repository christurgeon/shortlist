"""Zero-dep HTML assembly. Every interpolated value goes through HtmlBuilder.esc()."""
from __future__ import annotations

import html as _html

from .theme import BG, FG, GRID, rgb_hex


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


_CSS = f"""
body {{ background:{rgb_hex(BG)}; color:{rgb_hex(FG)};
        font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin:0; padding:20px; }}
h1 {{ font-size:20px; }} h2 {{ font-size:16px; margin-top:28px; }}
img.glance {{ max-width:100%; border-radius:6px; }}
table {{ border-collapse:collapse; margin:8px 0; font-size:13px; }}
td, th {{ padding:4px 8px; text-align:right; }}
td.k, th.k {{ text-align:left; color:#9fb0bd; }}
.card {{ border:1px solid {rgb_hex(GRID)}; border-radius:8px; padding:12px 16px; margin:12px 0; }}
.bull {{ color:#7fc99a; }} .bear {{ color:#e08f8f; }} .flag {{ color:#e0b86a; }}
.muted {{ color:#7b8a97; font-size:12px; }}
"""


def document(title: str, png_b64: str | None, body: str) -> str:
    b = HtmlBuilder()
    glance = (f'<img class="glance" src="data:image/png;base64,{png_b64}" '
              f'alt="dashboard">' if png_b64 else "")
    return (f"<!DOCTYPE html>\n<html><head><meta charset='utf-8'>"
            f"<title>{b.esc(title)}</title><style>{_CSS}</style></head>"
            f"<body><h1>{b.esc(title)}</h1>{glance}{body}</body></html>")
