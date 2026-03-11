# arxiv-radar Configuration
# arxiv 论文追踪配置文件
# 
# ● 自然语言区域：模糊配置，用来描述你的研究兴趣和背景
# ● 参数表格区域：精准配置，带范围和影响说明

---

## 🧠 研究背景（自然语言 · 可随意修改）

我是一名计算机视觉研究者，主要方向是视觉表征和统一表征学习。
我关注图像 tokenization、视觉生成、多模态统一理解与生成等方向。
我希望追踪能与视觉表征（visual representation）和统一表征（unified representation）相关的最新工作。
我对 VQVAE、1D tokenizer、diffusion model、representation learning、unified model 比较感兴趣。
对纯文本 NLP、医学、金融、量化交易等领域的论文不感兴趣。

---

## 🔬 追踪领域（Domain Definitions）

每个域有名称、种子论文（用作语义锚点）、关键词。

### Domain 1: 1D Image Tokenizer

- **种子论文**: TiTok (arXiv:2406.07550) — "An Image is Worth 32 Tokens for Reconstruction and Generation"
- **关键词**: image tokenizer, 1D token, discrete representation, VQVAE, codebook, image generation, reconstruction, token efficiency

### Domain 2: Unified Understanding & Generation

- **种子论文**: BAGEL (arXiv:2505.14683) — "Emerging Properties in Unified Multimodal Pretraining"
- **关键词**: unified model, multimodal, image understanding, image generation, text-to-image, visual question answering, unified pretraining, joint training

---

## ⚙️ 精准参数表

| 参数 | 默认值 | 范围 | 说明 |
|------|--------|------|------|
| `similarity_threshold` | 0.35 | 0.1 ~ 0.8 | 与种子论文的语义相似度阈值，低于此值的论文被过滤。越高越严格。 |
| `threshold_mode` | `adaptive` | `fixed` / `adaptive` / `llm_judge` | fixed=固定阈值；adaptive=自动取当日top-N%；llm_judge=对borderline论文用LLM判断 |
| `adaptive_top_k` | 30 | 5 ~ 100 | adaptive模式下每域保留的文章数上限 |
| `llm_judge_range` | `0.25-0.45` | — | llm_judge模式中需要LLM判断的相似度区间 |
| `top_k_recommend` | 2 | 1 ~ 3 | 每个域的必读推荐数量 |
| `arxiv_categories` | `cs.CV,cs.LG,cs.AI` | arxiv分类 | 爬取的arxiv分类，逗号分隔 |
| `embedding_model` | `all-MiniLM-L6-v2` | — | sentence-transformers模型，建议用MiniLM（快）或mpnet（准） |
| `max_papers_per_day` | 500 | 100 ~ 2000 | 每日最多处理论文数（防止爆炸） |
| `report_output` | `file` | `file` / `stdout` / `discord` | 报告输出方式 |
| `report_path` | `/Users/lanlanlan/mydata/notes/01-Diary/科研追踪/` | 路径 | 报告保存目录（report_output=file时有效） |
| `noise_filter_strict` | `true` | `true` / `false` | 是否严格过滤医学/金融等噪声领域论文 |

---

## 🏆 VIP 作者列表

以下作者的论文会被特别标注 `[⭐ VIP]`：

```
# 计算机视觉大牛
Kaiming He
Saining Xie
Ross Girshick
Piotr Dollar
Jian Sun
Yann LeCun
Geoffrey Hinton
Ilya Sutskever
Andrej Karpathy
Sergey Levine

# 视觉生成/表征
Yang Song
Prafulla Dhariwal
Aditya Ramesh

# RAE 论文作者 (arXiv:2510.11690)
Boyang Zheng
Nanye Ma
Shengbang Tong

# 大组织
# 添加组织关键词用于标注
Meta AI Research
Google Brain
Google DeepMind
OpenAI
ByteDance Research
Microsoft Research
FAIR
```

---

## 🚫 噪声过滤关键词（预定义，可扩展）

以下关键词出现在标题/摘要中，论文会在语义过滤前被快速丢弃：

```
medical, clinical, patient, hospital, drug, cancer, tumor, diabetes
financial, stock, trading, cryptocurrency, portfolio, hedge
legal, law, contract, court
chemistry, molecular, protein, genome, DNA
agriculture, crop, soil
```
