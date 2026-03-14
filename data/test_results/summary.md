# A/B Test Summary — 2026-03-12

## Parse Success Rate

| Model       | Scheme A | Scheme B |
| ----------- | -------- | -------- |
| wq/claude46 | 0.5      | 1.0      |
| wq/glm5     | 1.0      | 1.0      |

## Fill Rate

| Model       | Scheme A | Scheme B |
| ----------- | -------- | -------- |
| wq/claude46 | 0.5      | 1.0      |
| wq/glm5     | 1.0      | 1.0      |

## Average Latency (s)

| Model       | Scheme A | Scheme B |
| ----------- | -------- | -------- |
| wq/claude46 | 16.4     | 30.6     |
| wq/glm5     | 71.6     | 73.84    |

## Contribution-Type Enum Valid Rate

| Model       | Scheme A | Scheme B |
| ----------- | -------- | -------- |
| wq/claude46 | 0.5      | 1.0      |
| wq/glm5     | 1.0      | 1.0      |

## Sample Output

### Scheme A — best model: `wq/glm5`

**Paper:** `2603.06449`  |  **Latency:** 56.546s

- **cn_oneliner:** 提出CaTok，利用MeanFlow解码器实现一维因果图像标记，兼顾生成速度与质量。
- **contribution_type:** `significant`
- **editorial_note:** 该论文提出了MeanFlow解码器以解决视觉自回归建模中因果性与生成质量的权衡问题，方法具有实质创新性。
- **why_read:** 推荐阅读，因为它为将大语言模型（LLM）的范式无缝迁移至视觉生成领域提供了关键的因果量化解决方案。
- **method_variants:**
  - `flow:mean-flow-decoder` — 提出MeanFlow目标，通过时间间隔选择绑定token，实现支持因果推断的流匹配解码器。
  - `tokenizer:1d-causal` — 设计了严格的一维亚因果分词器，克服了二维展平或启发式排序对自回归建模的不利影响。

### Scheme B — best model: `wq/claude46`

**Paper:** `2603.06449`  |  **Latency:** 44.408s

- **cn_oneliner:** CaTok：用MeanFlow解码器实现因果1D图像tokenization，支持单步快速生成与高保真多步采样
- **contribution_type:** `significant`
- **editorial_note:** 将MeanFlow目标与因果1D tokenization结合是较具创意的设计，填补了AR视觉生成中因果tokenizer与高质量解码器之间的空白；若实验验证充分，对视觉AR模型社区有实际推动价值。MeanFlow引入视觉tokenization的跨域迁移值得关注，但需审视与TiTok等已有1D tokenizer的性能差距是否显著。
- **why_read:** 如果你在研究自回归视觉生成或视觉tokenizer设计，CaTok提供了一个将因果序列建模与flow-based高质量解码统一的新框架，值得精读。
- **method_variants:**
  - `titok:causal-rewrite` — 在TiTok风格的1D token序列基础上引入因果约束，使token序列满足自回归next-token prediction的因果顺序要求。
  - `flow-matching:meanflow-decoder` — 将MeanFlow目标（基于时间区间的平均速度场）作为图像解码器的训练目标，替代标准扩散或VQVAE解码，实现单步与多步生成的统一。
  - `diffusion-autoencoder:causal-token-conditioning` — 将扩散autoencoder的解码器条件从全token改为因果有序token，并用MeanFlow目标替代nested dropout机制以消除token间不平衡。
