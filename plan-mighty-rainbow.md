# GA 记忆系统：Embedding 语义检索 + 增量萃取

## Context

当前 GA 的记忆系统（`memory_auto.py`）基于**纯关键词匹配**进行记忆召回，在以下场景存在明确天花板：

1. **语义鸿沟**：用户说"最近 deadline 好焦虑"，L2 存有"压力大时喜欢听古典音乐放松"——关键词匹配零命中
2. **规模膨胀致噪音**：L2 到 30+ 条后，`"偏好"` 一词平等命中 `颜色偏好`、`饮食偏好`、`IDE偏好`……Top-5 结果中 3/5 跟当前 query 无关，污染上下文
3. **萃取时全量传 L2 给 LLM**：`extract_facts()` 将整个 `global_mem.txt` 注入 prompt。200 条事实 ≈ 3000+ tokens，随使用持续膨胀

核心矛盾：**关键词匹配缺乏语义区分度**。需要引入本地 embedding 模型，在检索端做语义过滤，在萃取端做增量去重。

## 方案

### 新增依赖

```
sentence-transformers  (或 onnxruntime + tokenizers，用于加载 all-MiniLM-L6-v2)
```
选择 `all-MiniLM-L6-v2`（80MB，384 维，中文支持好，纯 CPU 推理 ~10ms）。

### 架构

```
新文件: memory/embedder.py     ← embedding 模型加载 + 推理
修改:   memory/memory_auto.py  ← search_memory 升级为语义检索
                               extract_facts 升级为增量萃取
                               auto_update_l2 同步维护 vectors.json

新文件: memory/vectors.json    ← 向量索引缓存（可从 global_mem.txt 重建）


数据流:

写入时（会话结束，离线）:
  auto_update_l2(facts)
    ├── 写入 global_mem.txt          ← 主存储（不变）
    └── 重建 vectors.json            ← 对每条 fact_text embed 并缓存


检索时（每次用户输入）:
  search_memory(query, memory_dir)
    ├── embed(query)
    ├── 读 vectors.json → cosine(query_vec, each fact_vec)
    ├── 过滤 score > THRESHOLD (默认 0.4)
    ├── 取 top-5
    └── 返回格式化文本（格式不变）


萃取时（会话结束）:
  extract_facts(conversation, ...)
    ├── embed(conversation_text)
    ├── 读 vectors.json → top-20 与对话最相关的事实
    ├── 只将这 ≤20 条 + 对话文本 → LLM
    └── LLM 返回新增/更新 → auto_update_l2()
```

### 关键设计决策

| 问题 | 决策 |
|------|------|
| embed 什么文本 | `"[section] key: value"` —— key提供维度锚点，value提供具体内容，section提供分类信息 |
| vectors.json vs global_mem.txt | **主从关系**。txt 是 source of truth，vectors.json 是机器缓存，可随时从 txt 重建 |
| 检索阈值 | 相关性 > 0.4（经验值，可通过 benchmark 调参） |
| 萃取传多少相关事实 | top-20（硬上限，防止 token 爆炸） |
| embedding 模型加载 | 单例，启动时加载一次，常驻内存 |
| 中文 2-gram 旧逻辑 | **保留但不作为主路径**。embedding 路径优先，2-gram 作为冷启动时的 fallback（模型未安装时） |

---

## 实现步骤

### Step 1: 创建 `memory/embedder.py`

- 封装 `sentence-transformers` 的加载和推理
- `Embedder` 类：`__init__` 加载模型，`encode(text)` 返回 numpy vector
- 全局单例 `get_embedder()` 
- Graceful degradation：模型未安装时返回 None，search_memory 降级为旧关键词逻辑

### Step 2: 创建 `memory/vectors.py`（向量索引管理）

- `build_vectors(l2_path, embedder)`: 解析 global_mem.txt 所有事实 → embed → 输出 list[dict]
- `save_vectors(vectors, vec_path, l2_path)`: 写入 vectors.json（含 l2_mtime 元数据）
- `load_vectors(vec_path, embedder, l2_path)`: 读取 vectors.json；若 l2_mtime 不匹配 → 自动重建
- `search_vectors(query, vectors, embedder, top_k=5, threshold=0.4)`: 余弦相似度检索

### Step 3: 升级 `memory_auto.py`

**`search_memory` 升级**：
- 尝试加载 embedder + vectors
- 成功 → 语义检索，返回 top-5（score > 0.4）
- 失败 → 降级为旧关键词逻辑（保留现有代码为 fallback）
- 返回格式不变：`[Auto Retrieved Memory]\n- [section] text`

**`extract_facts` 升级**：
- 新增参数逻辑：不再传整个 `existing_l2`
- 改为：用 conversation 的 embedding 检索 top-20 相关事实
- 只将这 20 条作为 "当前已存储的相关事实" 注入 LLM prompt
- 其余逻辑（JSON 解析、返回格式）不变

**`auto_update_l2` 升级**：
- 写入 global_mem.txt 后，调用 `build_vectors + save_vectors` 重建索引
- 原有逻辑（去重、冲突检测、section 组织）完全不变

### Step 4: Benchmark 对比

构造新脚本 `benchmark/test_embedding.py`，复用 `benchmark/test_noise.py` 的 heavy user 场景：

- 用 31 条事实填充 L2
- 同时跑 `search_memory_old`（关键词）和 `search_memory_new`（embedding）
- 对比 4 个 query 的检索结果，量化噪音率

---

## 测试用例

### 用例 1：噪音对比（关键词 vs Embedding）

```
L2: 31 条事实（同 test_noise.py）
Query: "帮我推荐几件衣服"

旧方案（关键词）:
  ✅ 颜色偏好: 白色系
  ✅ 风格偏好: 简约风格
  ❌ 饮食偏好: 川菜        ← 噪音
  ❌ 饮品偏好: 手冲咖啡     ← 噪音
  ❌ 音乐偏好: 古典音乐     ← 噪音
  噪音率: 3/5 = 60%

新方案（embedding）:
  ✅ 颜色偏好: 白色系       ← score 0.82
  ✅ 风格偏好: 简约风格      ← score 0.78
  噪音率: 0/2 = 0%  (score阈值过滤掉了无关事实)
```

### 用例 2：语义鸿沟

```
L2: 包含 "压力大时喜欢听古典音乐放松"
Query: "最近 deadline 快到了，好焦虑"

旧方案（关键词）:
  (无匹配) 或仅匹配到"项目"相关的噪音

新方案（embedding）:
  ✅ 音乐偏好: 古典音乐     ← "焦虑"与"压力大时放松"语义相近
```

### 用例 3：萃取 Token 对比

```
L2: 31 条事实
对话: "我最近想学 Go 语言，之前一直用 Python"

旧方案 extract_facts:
  传给 LLM: 31 条 × ~40 chars ≈ 1240 chars ≈ ~500 tokens

新方案 extract_facts:
  检索 top-20: 其中 18 条与"技术栈学习"无关被过滤
  传给 LLM: ~3 条 × ~40 chars ≈ 120 chars ≈ ~50 tokens
  Token 节省: ~90%
```

---

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `memory/embedder.py` | 新增 | Embedding 模型封装 |
| `memory/vectors.py` | 新增 | 向量索引管理（构建/保存/加载/检索） |
| `memory/memory_auto.py` | 修改 | search_memory/extract_facts/auto_update_l2 升级 |
| `benchmark/test_embedding.py` | 新增 | 新旧方案对比测试 |
| `pyproject.toml` | 修改 | 新增 sentence-transformers 可选依赖 |

---

## 验证方式

1. **单元测试**: `python benchmark/test_noise.py` → 验证旧方案噪音率
2. **新方案测试**: `python benchmark/test_embedding.py` → 对比检索精度 + Token 节省
3. **E2E 测试**: `python benchmark/run_benchmark.py e2e` → 确认 4 个跨会话用例仍然通过
4. **降级测试**: 不安装 sentence-transformers → 确认 search_memory 回退到关键词逻辑

---

## 不做的

- 不做向量数据库（FAISS/Milvus/ChromaDB）——31~200 条用 numpy 暴力计算足够
- 不换 embedding 模型（先跑通 all-MiniLM-L6-v2，后续可换 BGE 等中文模型）
- 不做 L3/L4 的自动检索（后续 PR）
- 不做门控逻辑（后续 PR）
