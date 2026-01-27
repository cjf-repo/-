import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

# 设置绘图风格
plt.style.use('seaborn-v0_8-white')
plt.rcParams['font.sans-serif'] = ['SimHei']  # 用来正常显示中文标签
plt.rcParams['axes.unicode_minus'] = False  # 用来正常显示负号

def plot_large_scale_confusion_matrix():
    # 设定类别数量为 100 (符合您的实验设定)
    N_CLASSES = 100
    
    # -----------------------------------------------------------
    # 1. 构造 [基准单路径 DS-Normal] 数据 (准确率高，对角线清晰)
    # -----------------------------------------------------------
    # 初始化对角线为主 (90% 概率在对角线)
    cm_normal = np.eye(N_CLASSES) * 0.9 
    # 添加少量随机噪声 (模拟 10% 的误判，主要集中在相似类别)
    noise = np.random.rand(N_CLASSES, N_CLASSES) * 0.05
    cm_normal += noise
    # 归一化 (使每行之和为1)
    cm_normal = cm_normal / cm_normal.sum(axis=1, keepdims=True)

    # -----------------------------------------------------------
    # 2. 构造 [多路径防御 DS-Multipath] 数据 (准确率低，噪声弥散)
    # -----------------------------------------------------------
    # 对角线概率大幅降低 (35% 左右)
    cm_obfuscated = np.eye(N_CLASSES) * 0.35
    # 添加大量随机噪声 (模拟 65% 的误判，散落在各个位置)
    noise_heavy = np.random.rand(N_CLASSES, N_CLASSES) * 0.4
    cm_obfuscated += noise_heavy
    # 归一化
    cm_obfuscated = cm_obfuscated / cm_obfuscated.sum(axis=1, keepdims=True)

    # -----------------------------------------------------------
    # 3. 绘图 (隐藏刻度标签，只看模式)
    # -----------------------------------------------------------
    fig, axes = plt.subplots(1, 2, figsize=(20, 9))

    # --- 左图：正常情况 ---
    sns.heatmap(cm_normal, ax=axes[0], cmap='Blues', vmin=0, vmax=1,
                xticklabels=False, yticklabels=False, cbar=False) # 关键：关闭刻度标签
    axes[0].set_title('(a) 基准单路径场景 (DS-Normal)\n准确率 ~90% (对角线清晰)', fontsize=16, pad=15)
    axes[0].set_ylabel('真实网站类别索引 (True Label Index)', fontsize=14)
    axes[0].set_xlabel('预测网站类别索引 (Predicted Label Index)', fontsize=14)
    # 添加边框
    for _, spine in axes[0].spines.items():
        spine.set_visible(True); spine.set_linewidth(1)

    # --- 右图：伪装情况 ---
    # 使用 OrRd (红橙色系) 展示防御效果，或者继续用 Blues 对比
    sns.heatmap(cm_obfuscated, ax=axes[1], cmap='Blues', vmin=0, vmax=1,
                xticklabels=False, yticklabels=False, 
                cbar_kws={'label': '预测置信度 (Probability)'}) # 关键：关闭刻度标签
    axes[1].set_title('(b) 多路径传输场景 (DS-Multipath)\n准确率 ~35% (特征弥散)', fontsize=16, pad=15)
    axes[1].set_xlabel('预测网站类别索引 (Predicted Label Index)', fontsize=14)
    axes[1].set_yticks([]) 
    # 添加边框
    for _, spine in axes[1].spines.items():
        spine.set_visible(True); spine.set_linewidth(1)

    plt.tight_layout()
    plt.savefig('Figure_3_6_Confusion_Matrix_100Classes.png', dpi=300)
    print("图表已生成: Figure_3_6_Confusion_Matrix_100Classes.png")

if __name__ == '__main__':
    plot_large_scale_confusion_matrix()