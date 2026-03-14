# G/H Prompt Test — 进行日志

> 本次测试目的：测试 Scheme G（最小修改Variant F）与 Scheme H（G+in-prompt自查）在 core_cite 幻觉率上的差异，确定最终 prompt 方案。

---

## 测试上下文（Pipeline阶段定位）

```
全流程:
  [前序-已模拟] S2 爬取 → 写入DB（seed=11篇 + s2_expansion=其引用）
  [本次测试]    arxiv_id + 预下好的LaTeX → paper_analyst分析 → 幻觉验证
  [下一步]      仅 arxiv_id 的完整能力测试（不预下LaTeX）
  [之后]        分析结果写回补充DB
```

## 测试配置

- **模型**: minimaxm25
- **测试论文（11篇）**: 2406.07550, 2501.07730, 2503.08685, 2503.10772, 2504.08736, 2505.12053, 2505.21473, 2506.05289, 2507.08441, 2511.20565, 2601.01535
- **验证方式**: DB 自身引用匹配（`paper_edges WHERE src_id=paper AND edge_type=CITES JOIN papers`），Jaccard > 0.5 + 词数 ≤ 3 过滤
- **DB**: `data/paper_network.db`（重建，11篇seed + S2 references）

## Prompt 差异

| 字段 | Scheme G | Scheme H |
|------|----------|----------|
| 基础 | Variant F + method_variants格式 + role单值 | 同G |
| 自查段落 | 无 | 提交前对照reference list验证，不确定请arxiv搜索 |

---

## 运行记录

### 2026-03-13

#### 阶段一：DB 重建
- [x] `build_test_db.py` 运行完成 — 01:28
- [x] DB 统计: 11 seeds + 629 s2_expansion refs，629 CITES edges，idx_edges_src 已建

#### 阶段二：G/H 测试
- [x] G Round1 完成 01:28-01:46 — **幻觉率 58.7%（61/104）**
- [x] G Round2 完成（重跑幻觉篇）— **幻觉率 55.7%（54/97）**
- [x] H 完成 — **幻觉率 59.4%（63/106）**
- [x] report_gh.html 生成 01:58（93KB）

#### 关键发现（01:58）
- G1=58.7% / G2=55.7% / H=59.4% — 三种 prompt 差异很小
- H（in-prompt 自查）**不优于** G，甚至略差（+0.7%）
- G2（带幻觉警告重跑）对部分论文有效，对部分论文无效（2511 8→8 不变）
- **3 次 parse failure**（core_cite=0，minimaxm25 输出无法解析）：
  - H-2501.07730, G2-2505.12053, G1-2506.05289
- **逐篇差异大**：2406.07550（2/10，相对最好） vs 2503.08685（8/10，最差）
- **⚠️ 验证 false positive 问题**：suspicious 标题包含真实论文（标题略有出入），不代表全是幻觉
  - 真幻觉特征：过度通用标题（"Autoregressive Models"）、括号集合体（"Masked Generative Models (Muse, MaskBit, Meissonic)"）、明显错误subtitle
  - 误报特征：LLM 对已知论文的标题 paraphrase（"LlamaGen: Scalable Efficient Image Generation Training" vs 实际标题）

#### ⚠️ 已知问题
- 验证误报率高：Jaccard 对标题 paraphrase 不鲁棒，DB 覆盖也不完整（629条不覆盖所有被引文献）
- 3 parse failure 需重跑或手动 review

---

## 已知问题

| # | 时间 | 问题 | 状态 |
|---|------|------|------|
| 1 | 2026-03-12 | minimaxm25 2506.05289: SiT-XL/MAR-B等模型名作为论文标题（over-fixed prompt导致） | 本次重测 |
| 2 | 2026-03-12 | TiTok幻觉标题（错误标题"TiTok: An Efficient Tokenizer..."） | 本次测试验证 |
| 3 | 2026-03-12 | method_variants: role combined写法（`extends/uses`），已在G/H中约束 | 本次测试验证 |

---

## TODO（测试结束后）

- [ ] `semantic_scholar.py`: `get_paper()` + `get_references()` 合并为单次 API call（3处调用点: init_graph.py, context_injector.py, build_edges()）
- [ ] 实现 `post_process.py`：core_cite title → arxiv_id 匹配 + CORE_CITE 边写入 DB
- [ ] 更新 `main.py` 调用 `paper_analyst.analyze_paper()`
- [ ] 归档旧脚本到 `scripts/archive/`：scheme_a.py, test_schemes.py
