"""Throwaway brainstorming mockups — render scout report layouts from REAL ScoreCards."""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import cm, colors
import numpy as np

HERE = Path(__file__).parent
cards = json.load(open(HERE / "cards.json"))
cards.sort(key=lambda c: c["composite"], reverse=True)

SUBS = ["quality", "moat", "growth", "value", "momentum", "insider", "risk"]
SUB_LBL = ["Qual", "Moat", "Grow", "Value", "Mom", "Insdr", "Risk"]
SESSION = "2026-06-04"
BG = "#17212b"; FG = "#e9edef"; ACCENT = "#5ea6e0"; GRID = "#2b3947"
cmap = cm.get_cmap("RdYlGn").copy()
cmap.set_bad("#33404d")  # abstained / None sub-score -> neutral gray
norm = colors.Normalize(vmin=0, vmax=100)

def mat(rows):
    """[ticker x subscore] with None -> NaN so masked legs render gray."""
    return np.array([[(c[s] if c[s] is not None else np.nan) for s in SUBS] for c in rows],
                    dtype=float)

def cell_color(v):
    return cmap(norm(v))

# ---------- Mockup A: ranked heatmap table ----------
def heatmap():
    n = len(cards)
    fig, ax = plt.subplots(figsize=(7.6, 0.62 * n + 1.4), dpi=150)
    fig.patch.set_facecolor(BG); ax.set_facecolor(BG)
    M = mat(cards)
    ax.imshow(M, cmap="RdYlGn", vmin=0, vmax=100, aspect="auto")
    for i in range(n):
        for j in range(len(SUBS)):
            v = M[i, j]
            ax.text(j, i, "·" if np.isnan(v) else f"{v:.0f}", ha="center", va="center",
                    color="#9fb0bd" if np.isnan(v) else "#11181f", fontsize=10, fontweight="bold")
    # ticker + composite labels on the left
    ax.set_yticks(range(n))
    ax.set_yticklabels([f"{c['ticker']}  {c['composite']:.0f}" for c in cards],
                       color=FG, fontsize=11, fontweight="bold")
    ax.set_xticks(range(len(SUBS)))
    ax.set_xticklabels(SUB_LBL, color=FG, fontsize=10)
    ax.tick_params(length=0)
    for sp in ax.spines.values():
        sp.set_visible(False)
    # flag/gate annotations to the right
    for i, c in enumerate(cards):
        tags = []
        if c["gates"]:
            tags.append("⛔" + ",".join(c["gates"]))
        if c.get("upside_to_target") is not None:
            tags.append(f"↗{c['upside_to_target']*100:+.0f}%")
        if c.get("thin"):
            tags.append("thin")
        if tags:
            ax.text(len(SUBS) - 0.4, i, "  " + " · ".join(tags), ha="left", va="center",
                    color="#9fb0bd", fontsize=8)
    ax.set_xlim(-0.5, len(SUBS) + 1.8)
    ax.set_title(f"Scout shortlist — {SESSION}", color=FG, fontsize=14,
                 fontweight="bold", loc="left", pad=12)
    fig.text(0.012, 0.02, "7 sub-scores, 0–100 · ranked by composite · red→green heatmap",
             color="#7b8a97", fontsize=8)
    fig.tight_layout(rect=[0, 0.03, 1, 1])
    fig.savefig(HERE / "mockup_a_heatmap.png", facecolor=BG, bbox_inches="tight")
    plt.close(fig)

# ---------- Mockup B: radar small-multiples (top 3) ----------
def radar():
    top = cards[:3]
    ang = np.linspace(0, 2 * np.pi, len(SUBS), endpoint=False).tolist()
    ang += ang[:1]
    fig, axes = plt.subplots(1, 3, figsize=(9, 3.6), dpi=150,
                             subplot_kw=dict(polar=True))
    fig.patch.set_facecolor(BG)
    for ax, c in zip(axes, top):
        vals = [(c[s] or 0) for s in SUBS]; vals += vals[:1]
        ax.set_facecolor(BG)
        ax.plot(ang, vals, color=ACCENT, linewidth=2)
        ax.fill(ang, vals, color=ACCENT, alpha=0.30)
        ax.set_xticks(ang[:-1]); ax.set_xticklabels(SUB_LBL, color=FG, fontsize=8)
        ax.set_yticks([25, 50, 75]); ax.set_yticklabels([], color="#7b8a97")
        ax.set_ylim(0, 100)
        ax.grid(color=GRID)
        ax.spines["polar"].set_color(GRID)
        ax.set_title(f"{c['ticker']}  ·  {c['composite']:.0f}", color=FG,
                     fontsize=12, fontweight="bold", pad=14)
    fig.suptitle(f"Scout leaders — {SESSION}", color=FG, fontsize=14,
                 fontweight="bold", x=0.07, ha="left")
    fig.tight_layout(rect=[0, 0, 1, 0.92])
    fig.savefig(HERE / "mockup_b_radar.png", facecolor=BG, bbox_inches="tight")
    plt.close(fig)

# ---------- Mockup C: composite dashboard ----------
def dashboard():
    n = len(cards)
    fig = plt.figure(figsize=(7.8, 6.4), dpi=150)
    fig.patch.set_facecolor(BG)
    gs = fig.add_gridspec(2, 1, height_ratios=[1.1, 1.5], hspace=0.35)
    # top: composite bars colored by value + upside annotation
    ax1 = fig.add_subplot(gs[0]); ax1.set_facecolor(BG)
    ys = range(n)
    comps = [c["composite"] for c in cards]
    ax1.barh(list(ys), comps, color=[cell_color(v) for v in comps], height=0.6)
    ax1.set_yticks(list(ys))
    ax1.set_yticklabels([c["ticker"] for c in cards], color=FG, fontsize=11, fontweight="bold")
    ax1.invert_yaxis()
    ax1.set_xlim(0, 100)
    for i, c in enumerate(cards):
        up = c.get("upside_to_target")
        lab = f"{c['composite']:.0f}" + (f"   ↗{up*100:+.0f}% to target" if up is not None else "")
        ax1.text(c["composite"] + 1.5, i, lab, va="center", color="#cdd8e0", fontsize=9)
    ax1.set_title("Composite", color=FG, fontsize=12, fontweight="bold", loc="left")
    for sp in ax1.spines.values(): sp.set_color(GRID)
    ax1.tick_params(colors="#7b8a97", length=0)
    ax1.set_xticks([0, 25, 50, 75, 100])
    # bottom: heatmap of sub-scores
    ax2 = fig.add_subplot(gs[1]); ax2.set_facecolor(BG)
    M = mat(cards)
    ax2.imshow(M, cmap="RdYlGn", vmin=0, vmax=100, aspect="auto")
    for i in range(n):
        for j in range(len(SUBS)):
            v = M[i, j]
            ax2.text(j, i, "·" if np.isnan(v) else f"{v:.0f}", ha="center", va="center",
                     color="#9fb0bd" if np.isnan(v) else "#11181f", fontsize=9, fontweight="bold")
    ax2.set_yticks(range(n)); ax2.set_yticklabels([c["ticker"] for c in cards],
                                                  color=FG, fontsize=10, fontweight="bold")
    ax2.set_xticks(range(len(SUBS))); ax2.set_xticklabels(SUB_LBL, color=FG, fontsize=9)
    ax2.tick_params(length=0)
    for sp in ax2.spines.values(): sp.set_visible(False)
    ax2.set_title("Sub-scores", color=FG, fontsize=12, fontweight="bold", loc="left")
    fig.suptitle(f"Scout daily dashboard — {SESSION}", color=FG, fontsize=14,
                 fontweight="bold", x=0.06, ha="left")
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(HERE / "mockup_c_dashboard.png", facecolor=BG, bbox_inches="tight")
    plt.close(fig)

heatmap(); radar(); dashboard()
print("rendered:", *[p.name for p in sorted(HERE.glob("mockup_*.png"))])
