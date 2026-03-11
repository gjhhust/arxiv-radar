# Threshold Tuning Notes
## arxiv-radar Semantic Filter — Empirical Findings

*From standalone test (18 papers) and E2E test (30 real arxiv papers)*

---

## 模型选择

| 模型 | 速度 | 精度 | 推荐场景 |
|------|------|------|----------|
| `all-MiniLM-L6-v2` | ⚡ 快 (MPS ~1秒/100篇) | ★★★☆ | 日常使用，推荐 |
| `all-mpnet-base-v2` | 🐢 慢 3x | ★★★★ | 更重要的场合 |
| `paraphrase-MiniLM-L6-v2` | ⚡ 快 | ★★☆☆ | 不推荐，语义相似度偏差较大 |

**结论：默认用 `all-MiniLM-L6-v2`，精度和速度平衡最好。**

---

## 相似度得分分布（真实数据观察）

基于 2026-03-10 cs.CV 真实论文的测试：

### 明显相关论文（得分区间）
- 强相关（直接方向）：**0.70 - 0.90**
- 相关（同大方向）：**0.50 - 0.70**
- 弱相关（扩展方向）：**0.35 - 0.50**

### 噪声论文（pre-filter 前）
- 医学图像：**0.25 - 0.45**（注意：医学图像也是 cs.CV，部分会混入！）
- 金融/文本 NLP：**0.15 - 0.30**
- 其他领域：**0.10 - 0.35**

---

## 阈值设置建议

### ✅ 推荐配置（日常使用）

```markdown
| similarity_threshold | 0.40 | 0 ~ 1 | 建议从 0.35 提高到 0.40 |
| threshold_mode | adaptive | adaptive/fixed/hybrid | |
| adaptive_top_k | 20 | 5 ~ 50 | 每日论文量大时降低 |
```

**原因：**
- 0.35 偏宽松，会纳入一些扩展相关文章（如 token-based motion retrieval）
- 0.40 是更好的平衡点，保留真正重要的工作

### 🔬 精准配置（只要核心相关）

```markdown
| similarity_threshold | 0.50 | | 只保留强相关 |
| threshold_mode | adaptive | | |
| adaptive_top_k | 10 | | 每域最多10篇 |
```

### 📊 宽松配置（不想错过任何）

```markdown
| similarity_threshold | 0.30 | | 低门槛 |
| threshold_mode | adaptive | | |
| adaptive_top_k | 50 | | 每域最多50篇 |
```

---

## 关键发现

### ⚠️ 1. 医学图像分类的特殊情况

`cs.CV` 类别包含大量医学图像分析论文（CT scan, pathology 等）。这些论文：
- 可能绕过关键词 pre-filter（如果摘要没直接提医学词汇）
- 语义得分通常在 0.30-0.45 区间
- **解决方案**：把 `similarity_threshold` 设到 0.40 以上；或扩充噪声关键词列表

### 💡 2. Adaptive 模式 vs Fixed 模式

- **Adaptive**（推荐）：每天保留 top-K，动态适应每日论文质量分布
- **Fixed**：某天论文质量普遍低时，可能什么都保留不了；质量高时保留太多

### 💡 3. 种子论文摘要的质量很重要

- 好的种子摘要：包含大量领域特征词（VQVAE, codebook, 1D token 等）
- 如果某个域的 recall 不够好，考虑：
  1. 丰富种子摘要（可以合并多篇论文摘要）
  2. 在关键词字段加更多词汇

### 💡 4. 跨域论文处理

一篇论文会被分配到**得分最高的域**（winner-takes-all）。如果一篇论文与两个域都高度相关，只会在得分更高的域出现。

---

## 自适应阈值方案（待实现）

下一步可以探索的改进：

1. **基于近期均值的自适应**：维护一个近 7 天的相似度均值，以均值 * 0.8 作为动态阈值
2. **LLM judge 精细化**：对得分在 [0.30, 0.50] 的 borderline 论文，用轻量级 LLM（gpt-4o-mini）判断是否真正相关
3. **用户反馈学习**：记录用户标记"好文" / "差文"，微调相似度权重
4. **关键词强化**：对命中配置关键词的论文给予额外得分 boost (+0.05)

---

## 性能基准

机器：MacBook Pro (Apple Silicon MPS)

| 论文数量 | 加载模型 | 嵌入时间 | 过滤时间 | 总耗时 |
|---------|---------|---------|---------|------|
| 30 | ~5s（首次） | ~1s | ~0.1s | ~6s |
| 300 | ~1s（缓存） | ~3s | ~0.5s | ~5s |
| 500 | ~1s（缓存） | ~5s | ~1s | ~7s |

**结论：整个pipeline（含爬取）约 30-60 秒，非常快。**
