# LLM Analyse Module v3 Integration Plan

> 将 H3 测试成功的模块嵌入 arxiv-radar 生产架构
> 
> **日期**: 2026-03-14
> **状态**: 待实现

---

## 一、目标

将 H3 模板测试成功的模块嵌入 arxiv-radar 生产架构：

- **输入**: `arxiv_id` + `title`
- **输出**: 结构化分析 JSON（H3 模板 + 后验证 + session 溯源 + 重试机制 + 稳定 JSON 读取）

---

## 二、测试成功的模块

### 2.1 H3 模板

**位置**: `prompts/prompt_H3_template.txt`

**特点**:
- 基于字段结构设计，包含完整的分析字段
- 支持 `method_variants` 字段（3 个子字段：base_method, variant_tag, description）
- 支持 `core_cite` 字段（≥10 条引用，含 title/arxiv_id/role/note）
- 支持 `idea` 字段（3 条研究 idea）

### 2.2 测试结果

| 模型 | 格式 | 成功率 | 说明 |
|------|------|--------|------|
| **M25** | Anthropic | 100% (5/5) | ✅ 最佳表现 |
| **GLM5** | Anthropic | 100% (5/5) | ✅ 良好表现（需要 JSON 容错） |
| **GPT52** | OpenAI | 0% (0/2) | ❌ 权限/输出问题 |
| **Gemini31Pro** | OpenAI | 0% (0/1) | ❌ JSON 格式错误 |

**结论**: Anthropic 格式的模型（M25、GLM5）在处理复杂工具调用任务时表现明显优于 OpenAI 格式的模型。

### 2.3 关键代码

**容错 JSON 读取**（`tests/active/comparison_v3/run_h3_test.py`）:
- `fix_json_llm_output()` - 修复 LLM 生成的未转义双引号
- `safe_load_json()` - 容错加载 JSON 文件

**Session 溯源**:
- `load_sessions()` - 加载 sessions.json
- `find_transcript_path()` - 根据 label 查找 transcript 路径
- `add_transcript_to_result()` - 将 transcript 路径写入结果 JSON

---

## 三、嵌入架构设计

### 3.1 新增模块

```
arxiv-radar/scripts/
├── paper_analyst_v3.py       ← 新增：生产级入口模块
├── paper_analyst.py          ← 保留：现有模块（参考）
├── paper_db.py               ← 更新：添加 analysis 相关方法
└── config_parser.py          ← 更新：添加 analyst 配置项
```

### 3.2 数据库扩展

在 `papers` 表中新增字段：

```sql
ALTER TABLE papers ADD COLUMN analysis_status TEXT;      -- pending/analyzing/completed/failed
ALTER TABLE papers ADD COLUMN analysis_date TEXT;        -- 分析日期
ALTER TABLE papers ADD COLUMN analysis_model TEXT;       -- 使用模型
ALTER TABLE papers ADD COLUMN analysis_session_id TEXT;  -- session ID（溯源）
ALTER TABLE papers ADD COLUMN analysis_transcript TEXT;  -- transcript 路径（溯源）
ALTER TABLE papers ADD COLUMN analysis_result_path TEXT; -- 结果 JSON 路径
```

### 3.3 配置扩展

在 `config.md` 中新增配置项：

```markdown
## LLM Analyse (v3)
llm_analyse:
  enabled: true
  default_model: wq/minimaxm25
  fallback_model: wq/glm5
  max_retries: 1
  timeout_seconds: 300
  prompt_template: prompts/prompt_H3_template.txt
```

---

## 四、核心功能设计

### 4.1 入口函数

```python
def analyse_paper(arxiv_id: str, title: str) -> dict:
    """
    分析单篇论文（生产入口）
    
    Args:
        arxiv_id: arxiv 论文 ID
        title: 论文标题
    
    Returns:
        dict: 分析结果（包含所有 H3 字段 + 溯源信息）
    
    Raises:
        AnalysisError: 分析失败（重试后仍失败）
    """
    # 1. 检查是否已分析
    # 2. 调用 spawn_analyst()
    # 3. 等待结果
    # 4. 容错加载 JSON
    # 5. 后验证
    # 6. 更新 DB
    # 7. 返回结果
```

### 4.2 调用 paper-analyst Agent

```python
def spawn_analyst(arxiv_id: str, title: str, model: str) -> str:
    """
    Spawn paper-analyst agent to analyze a paper
    
    Args:
        arxiv_id: arxiv 论文 ID
        title: 论文标题
        model: 模型名称（如 wq/minimaxm25）
    
    Returns:
        str: 结果 JSON 文件路径
    
    Raises:
        SpawnError: spawn 失败
    """
    # 生成任务内容（基于 H3 模板）
    # 调用 sessions_spawn()
    # 等待完成
    # 返回结果路径
```

### 4.3 重试机制

```python
def analyse_with_retry(arxiv_id: str, title: str, max_retries: int = 1) -> dict:
    """
    带重试的分析
    
    Args:
        arxiv_id: arxiv 论文 ID
        title: 论文标题
        max_retries: 最大重试次数（默认 1 次）
    
    Returns:
        dict: 分析结果
    
    Raises:
        AnalysisError: 重试后仍失败
    """
    for attempt in range(max_retries + 1):
        try:
            # 尝试分析
            result = analyse_paper(arxiv_id, title)
            return result
        except Exception as e:
            if attempt < max_retries:
                # 重试
                continue
            else:
                raise AnalysisError(f"分析失败（重试 {max_retries} 次后）: {e}")
```

### 4.4 后验证

```python
def verify_analysis_result(result: dict, s2_refs: list) -> dict:
    """
    后验证：与 S2 引用列表对比
    
    Args:
        result: 分析结果
        s2_refs: S2 引用列表（来自 DB CITES 边）
    
    Returns:
        dict: 验证后的结果（包含 _verified, _matched, _similarities 字段）
    """
    # 提取 core_cite
    # 与 S2 引用列表做高相似度匹配（≥0.8）
    # 添加验证字段
    # 返回结果
```

### 4.5 稳定 JSON 读取

```python
def safe_load_json(file_path: str) -> dict:
    """
    安全加载 JSON 文件（容错处理）
    
    处理的问题:
    - LLM 生成的未转义双引号（GLM5 常见问题）
    - JSON 格式错误
    
    Args:
        file_path: JSON 文件路径
    
    Returns:
        dict: 解析后的数据
    
    Raises:
        JSONDecodeError: 修复后仍无法解析
    """
    # 已在 run_h3_test.py 中实现
```

---

## 五、调用流程

```
main.py / weekly.py
  │
  ├─ analyse_paper(arxiv_id, title)
  │
  ▼
paper_analyst_v3.py
  ├─ 检查 DB 是否已分析
  ├─ 生成任务内容（H3 模板）
  ├─ spawn_analyst()
  │   └─ sessions_spawn(agentId="paper-analyst", model="wq/minimaxm25")
  ├─ 等待完成
  ├─ safe_load_json()  ← 容错加载
  ├─ verify_analysis_result()  ← 后验证
  ├─ update_db_from_analysis()  ← 更新 DB
  └─ 返回结果
```

---

## 六、输出 JSON 格式

```json
{
  "arxiv_id": "2511.20565",
  "title": "DINO-Tok: Adapting DINO for Visual Tokenizers",
  "cn_oneliner": "基于 DINO 预训练视觉模型，引入双分支特征融合...",
  "cn_abstract": "本文提出 DINO-Tok...",
  "contribution_type": "significant",
  "editorial_note": "这篇论文建立在 DINOv2/v3 预训练视觉模型...",
  "why_read": "研究视觉 tokenizer 语义对齐与重建质量权衡的工程师...",
  "method_variants": [
    {
      "base_method": "vq-vae",
      "variant_tag": "vq-vae:pca-reweighted-distance",
      "description": "VQ-VAE 用标准 L2 距离查找最近邻..."
    }
  ],
  "core_cite": [
    {
      "title": "DINOv2: Learning Robust Visual Features without Supervision",
      "arxiv_id": "2304.07193",
      "role": "extends",
      "note": "本文直接使用冻结的 DINOv2 encoder..."
    }
  ],
  "idea": [
    {
      "title": "理论分析 PCA 排序与语义重要性的关联",
      "why": "本文实证发现 PCA 高特征值通道携带更多语义信息..."
    }
  ],
  "session_id": "0d881c64-9bec-4f11-8df3-0fdf5f5dc7be",
  "transcript_path": "~/.openclaw/agents/paper-analyst/sessions/0d881c64-9bec-4f11-8df3-0fdf5f5dc7be.jsonl",
  "session_time": "2026-03-14 22:51:33",
  "model": "wq/minimaxm25",
  "analysis_status": "completed"
}
```

---

## 七、实施步骤

1. **创建 feature 分支**: `feature/paper-analyst-v3`
2. **实现 `paper_analyst_v3.py`**: 包含所有核心功能
3. **更新 `paper_db.py`**: 添加 analysis 相关方法
4. **更新 `config_parser.py`**: 添加 analyst 配置项
5. **更新 `ARCHITECTURE.md`**: 文档更新
6. **测试**: 使用 5 篇论文测试
7. **PR**: 合并到 dev 分支

---

## 八、参考资料

- ARCHITECTURE.md - 架构文档
- tests/active/comparison_v3/run_h3_test.py - 测试脚本（包含容错 JSON 读取）
- prompts/prompt_H3_template.txt - H3 模板
- tests/active/comparison_v3/h3_glm5_report.html - 测试报告
