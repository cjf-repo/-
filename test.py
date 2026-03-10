# -*- coding: utf-8 -*-

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib import font_manager, rcParams

# Keep existing defense categories
# SMART corresponds to "本文" in your table
defenses = ["无防御", "WTF-PAD", "DFD", "BiMorphing", "TrafficSliver", "Our"]

# Reordered to match defenses above:
# [Undefended, WTF-PAD, DFD, BiMorphing, TrafficSliver-NET, SMART]
data = {
    "SVM": [50.6, 58.7, 26.9, 39.7, 21.4, 13.7],
    "RF": [89.3, 60.3, 27.3, 40.6, 20.9, 19.8],
    "CNN": [85.3, 64.9, 30.8, 45.3, 27.1, 24.6],
    "DF": [90.3, 66.4, 29.3, 51.6, 23.1, 22.6],
    "Var-CNN": [94.8, 71.3, 30.5, 53.6, 22.3, 15.2],
}

# Slightly deep colors, aligned with the current visual style
colors = {
    "SVM": "#8D6FBE",
    "RF": "#6FADE9",
    "CNN": "#D978B1",
    "DF": "#C86AA5",
    "Var-CNN": "#A983DE",
}

# ==============================
# Style knobs (edit these only)
# ==============================
X_GROUP_SPACING = 0.70      # Smaller value => categories are closer on x-axis
SPIKE_MAX_WIDTH = 0.10      # Smaller value => slimmer spikes
SPIKE_CURVATURE = 2.10      # Larger value => stronger inward-concave sides
SPIKE_STRETCH = 1.12        # Larger value => longer spike tip

X_CATEGORY_FONT = 7         # Font size for category names on x-axis
X_TICK_FONT = 7             # Tick label size on x-axis (keep same as category usually)
Y_TICK_FONT = 11            # Tick label size on y-axis
AXIS_LABEL_FONT = 8         # Font size for "Accuracy(%)" and "Defenses"
LEGEND_FONT = 10            # Font size for model legend text


def setup_chinese_font():
    """Pick an available CJK font to avoid garbled Chinese labels."""
    candidates = [
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK SC",
        "Noto Sans SC",
        "PingFang SC",
        "WenQuanYi Zen Hei",
        "Arial Unicode MS",
    ]
    available = {f.name for f in font_manager.fontManager.ttflist}
    chosen = next((name for name in candidates if name in available), None)

    if chosen:
        rcParams["font.sans-serif"] = [chosen, "DejaVu Sans"]
    else:
        # Keep fallback to avoid crash; install a CJK font if Chinese still cannot render.
        rcParams["font.sans-serif"] = ["DejaVu Sans"]

    rcParams["axes.unicode_minus"] = False


def draw_spike(ax, center, height, color, max_width=SPIKE_MAX_WIDTH, curvature=SPIKE_CURVATURE, stretch=SPIKE_STRETCH):
    """Draw a concave-in spike that narrows toward the apex."""
    if height <= 0:
        return

    # Slightly extend the visual tip while keeping all peaks inside the chart.
    peak_height = min(height * stretch, 99.0)
    y = np.linspace(0, peak_height, 360)
    t = y / peak_height

    # Inward-concave taper: two side edges get closer faster near the top.
    half_width = max_width * (1 - t) ** curvature

    ax.fill_betweenx(
        y,
        center - half_width,
        center + half_width,
        color=color,
        alpha=0.65,
        linewidth=0,
        zorder=2,
    )

    # Faint center stem to emphasize extension to the apex.
    ax.plot([center, center], [0, peak_height], color=color, linewidth=0.8, alpha=0.35, zorder=3)


setup_chinese_font()

fig, ax = plt.subplots(figsize=(6.4, 4.5), dpi=100)
fig.patch.set_facecolor("#FFFFFF")
ax.set_facecolor("#FFFFFF")

models = list(data.keys())
x = np.arange(len(defenses)) * X_GROUP_SPACING
offsets = np.linspace(-0.20, 0.20, len(models))

for i, model in enumerate(models):
    for j, value in enumerate(data[model]):
        draw_spike(ax, x[j] + offsets[i], value, colors[model])

ax.set_ylim(0, 100)
ax.set_xlim(x[0] - 0.5, x[-1] + 0.5)

ax.set_xticks(x)
ax.set_xticklabels(defenses, fontsize=X_CATEGORY_FONT)
ax.set_ylabel("Accuracy (%)", fontsize=AXIS_LABEL_FONT)
ax.set_xlabel("Defenses", fontsize=AXIS_LABEL_FONT, labelpad=12)
ax.tick_params(axis="x", labelsize=X_TICK_FONT, pad=6, colors="#444444")
ax.tick_params(axis="y", labelsize=Y_TICK_FONT, colors="#444444")

ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.spines["left"].set_color("#BBBBBB")
ax.spines["bottom"].set_color("#BBBBBB")

legend_handles = [
    Line2D(
        [0],
        [0],
        marker="^",
        linestyle="",
        markersize=8,
        markerfacecolor=colors[m],
        markeredgewidth=0,
        label=m,
    )
    for m in models
]
ax.legend(
    handles=legend_handles,
    loc="center right",
    frameon=False,
    fontsize=LEGEND_FONT,
    handlelength=0,
    handletextpad=0.5,
)

plt.tight_layout()
plt.savefig("defense_compare_fixed.png", dpi=600, bbox_inches="tight", facecolor="white", edgecolor="white")
# plt.show()

