"""
増本綺良（櫻坂46）公式ブログの年別投稿数・画像数を棒グラフにしてPNG保存する

使い方:
    pip install matplotlib
    python blog_year_chart.py
    → masumoto_blog_yearly.png が保存される
"""

import matplotlib.pyplot as plt
from matplotlib import font_manager

# ==========================================
# データ
# ==========================================
YEARS = ["2020", "2021", "2022", "2023", "2024", "2025", "2026"]
POSTS = [13, 51, 49, 63, 25, 24, 26]
IMAGES = [53, 254, 295, 468, 219, 199, 239]
PARTIAL_YEAR = "2026"  # 10月までの集計の年

OUTPUT = "masumoto_blog_yearly.png"

# ==========================================
# デザイン
# ==========================================
PINK = "#D9708F"        # 投稿数の棒（櫻坂ピンク・白背景で3:1以上のコントラスト）
PINK_LIGHT = "#F0B3C6"  # 画像数の棒（淡いピンク・数値ラベルで補う）
INK = "#2B2B2B"         # 本文の文字
INK_SUB = "#6B6B6B"     # 補足の文字
GRID = "#ECECEC"        # 目盛り線

# 日本語フォント（文字化け防止）: 使える最初のフォントを選ぶ
JP_FONTS = [
    "Hiragino Sans", "Hiragino Kaku Gothic ProN",  # Mac
    "Yu Gothic", "Meiryo",                          # Windows
    "Noto Sans CJK JP", "Noto Sans JP",             # Linux / Google
    "IPAexGothic", "IPAPGothic", "IPAGothic",
]
available = {f.name for f in font_manager.fontManager.ttflist}
font = next((f for f in JP_FONTS if f in available), None)
if font is None:
    raise SystemExit("日本語フォントが見つかりません。Noto Sans CJK JP などをインストールしてください。")
plt.rcParams["font.family"] = font


def draw_panel(ax, values, color, heading, unit):
    bars = ax.bar(YEARS, values, color=color, width=0.62, zorder=2)

    # 部分集計の年は斜線で区別
    for year, bar in zip(YEARS, bars):
        if year == PARTIAL_YEAR:
            bar.set_hatch("///")
            bar.set_edgecolor("white")
            bar.set_linewidth(0)

    # 各棒の上に数値
    top = max(values)
    for bar, v in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, v + top * 0.02, f"{v}",
                ha="center", va="bottom", fontsize=13, color=INK, fontweight="bold")

    ax.set_title(f"{heading}（{unit}）", loc="left", fontsize=14, color=INK, pad=12, fontweight="bold")
    ax.set_ylim(0, top * 1.18)
    ax.grid(axis="y", color=GRID, linewidth=1, zorder=0)
    ax.tick_params(axis="x", colors=INK, labelsize=12, length=0, pad=8)
    ax.tick_params(axis="y", colors=INK_SUB, labelsize=10, length=0)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#BDBDBD")

    # 2026年の目盛りに注記
    labels = [f"{y}\n(〜10月)" if y == PARTIAL_YEAR else y for y in YEARS]
    ax.set_xticks(range(len(YEARS)), labels)


fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.8, 7.2), dpi=150)
fig.patch.set_facecolor("white")

draw_panel(ax1, POSTS, PINK, "投稿数", "件")
draw_panel(ax2, IMAGES, PINK_LIGHT, "画像数", "枚")

# タイトル・アクセントの線
fig.text(0.06, 0.93, "増本綺良 ブログ年間投稿数", fontsize=24, color=INK, fontweight="bold")
fig.text(0.06, 0.885, "櫻坂46 公式ブログ／年別の投稿数と画像数", fontsize=13, color=INK_SUB)
fig.add_artist(plt.Line2D([0.06, 0.12], [0.865, 0.865], color=PINK, linewidth=4,
                          solid_capstyle="round"))

# 注記
fig.text(0.06, 0.03,
         f"※{PARTIAL_YEAR}年は10月までの集計（斜線）　合計：投稿 {sum(POSTS)} 件／画像 {sum(IMAGES)} 枚",
         fontsize=11, color=INK_SUB)

fig.subplots_adjust(left=0.08, right=0.97, top=0.78, bottom=0.16, wspace=0.22)
fig.savefig(OUTPUT, facecolor="white")
print(f"{OUTPUT} を保存しました（フォント: {font}）")
