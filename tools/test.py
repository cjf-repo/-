import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.font_manager import FontProperties

def pick_zh_font() -> FontProperties | None:
    # 自动寻找可用中文字体（按家族名匹配）
    preferred = [
        "Noto Sans CJK SC",
        "Noto Sans CJK",
        "Noto Serif CJK SC",
        "Noto Serif CJK",
        "WenQuanYi Zen Hei",
        "WenQuanYi Micro Hei",
        "Microsoft YaHei",
        "SimHei",
        "PingFang SC",
    ]
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in preferred:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name]
            plt.rcParams["axes.unicode_minus"] = False
            return FontProperties(family=name)
    return None


zh_font = pick_zh_font()
if zh_font is None:
    print("未找到可用的中文字体，将使用默认字体渲染，中文可能显示为方块。")

# 数据
paths = [1, 2, 3, 4]
svm = [50.6, 43.5, 37.2, 31.8]
rf = [89.3, 72.4, 58.6, 49.5]
cnn = [85.3, 70.1, 54.7, 45.2]
df = [90.3, 75.8, 60.3, 52.4]
varcnn = [94.8, 80.6, 64.9, 56.7]

# 画图
fig = plt.figure(figsize=(8.8, 5.6), dpi=220)
ax = fig.add_subplot(111)

ax.plot(paths, svm, marker='o', linewidth=2.2, markersize=6, label='SVM')
ax.plot(paths, rf, marker='s', linewidth=2.2, markersize=6, label='RF')
ax.plot(paths, cnn, marker='^', linewidth=2.2, markersize=6, label='CNN')
ax.plot(paths, df, marker='D', linewidth=2.2, markersize=6, label='DF')
ax.plot(paths, varcnn, marker='v', linewidth=2.2, markersize=6, label='VarCNN')

ax.set_xticks(paths)
ax.set_xticklabels(['1', '2', '3', '4'], fontsize=11, fontproperties=zh_font)
ax.tick_params(axis='y', labelsize=11)

ax.set_xlabel('路径数量', fontsize=12, fontproperties=zh_font)
ax.set_ylabel('识别准确率（%）', fontsize=12, fontproperties=zh_font)

# 不建议把“图3-10 ...”放进图内，论文里通常在正文里写图题
# 如果你确实想放标题，取消下面这行注释
# ax.set_title('图3-10 不同路径数量下流量识别准确率变化趋势', fontsize=13, fontproperties=zh_font, pad=12)

ax.grid(True, linestyle='--', linewidth=0.7, alpha=0.5)
ax.legend(frameon=True, fontsize=10, ncol=3)
ax.set_ylim(25, 100)
ax.set_xlim(0.9, 4.1)

fig.tight_layout()
plt.savefig("图3-10_不同路径数量下流量识别准确率变化趋势.png", bbox_inches='tight')
plt.show()
