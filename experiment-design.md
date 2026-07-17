# 实验设计：Agent 计划模式 vs 直答模式对比

**版本**：v1.0  
**日期**：2026-07-16 ~ 2026-07-17  

---

## 一、实验背景

### 1.1 研究问题

"计划模式"（Plan Mode）是 Agent 领域的一个重要概念——在 Agent 执行复杂任务前，先进行探索、规划，再按计划逐步执行。但与"直答模式"（Direct Mode，收到任务直接执行）相比，计划模式是否真正带来了成功率、效率和质量上的提升？这种提升在不同的 Agent 框架实现下是否一致？

### 1.2  GA 和 Hermes介绍

| 候选框架 | 语言 | 规模 | Plan Mode 实现 | 是否选用 | 原因 |
|------|:---:|:---:|------|:---:|------|
| **GenericAgent (GA)** | Python | ~3K 行 | 263行 `plan_sop.md` + 子Agent + 文件级 plan.md + 独立验证 | ✅ | 流程框架代表：重SOP、多Agent、文件持久化 |
| **Hermes Agent** | Python | 中型 | 无内置 Plan Mode，纯提示词驱动 + `todo` 内存工具 | ✅ | 认知框架代表：轻量引导、单Agent、纯提示词 |

### 1.3 参考框架：Agent 模式体系

根据 `Agent模式.md` 的分类，Agent 工作模式包括三大类 14 种，其中直答模式与计划模式：

| 分类 | 模式 | 适用场景 |
|------|------|---------|
| 🚀 核心执行 | 直答模式（Direct） | 1-2 步简单任务 |
| 🚀 核心执行 | **计划模式（Plan）** | 3 步以上、有依赖、需验证 |

计划模式的核心定义：**探索态 → 规划态 → 执行态 → 验证态**四阶段流程，适用于多步骤、有依赖关系、需要验证的复杂工程任务。Bug 修复被明确推荐使用计划模式作为首选。

---

## 二、Bug 选择

### 2.1 筛选标准

- **项目规模**：>100K LOC 的成熟开源项目，保证代码库有足够的复杂度
- **Bug 深度**：核心逻辑缺陷，非简单的配置错误或拼写错误，需要理解模块内部逻辑才能定位
- **修复范围**：多文件或单文件但逻辑复杂，涉及调用链追踪
- **有参考答案**：存在官方修复 commit，可验证 Agent 修复的正确性
- **可验证**：有明确的测试用例覆盖

从 GitHub 开源项目中初筛了 6 个复杂 bug，以 SQLAlchemy #11449 作为 Pilot（首个实验）。

### 2.2 Pilot Bug: SQLAlchemy #11449

| 属性 | 值 |
|------|-----|
| **Bug ID** | SQLAlchemy Issue #11449 |
| **项目** | SQLAlchemy（Python ORM 框架） |
| **项目规模** | ~200K LOC，20年历史，ORM 领域事实标准 |
| **Bug 简述** | `joinedload.innerjoin` 在自引用关联中因 `relationship()` 声明顺序不同产生错误的 SQL JOIN 结构 |
| **核心文件** | `lib/sqlalchemy/orm/strategies.py`（3344 行） |
| **核心方法** | `_splice_nested_inner_join`（~90 行递归逻辑） |
| **影响文件** | `strategies.py` +221/-68, `util.py` +2/-0, `test_eager_relations.py` +174/-0 |
| **参考修复** | Commit `d1394d9e0` |
| **Bug 类型** | 深度 Bug：单文件内递归逻辑缺陷，需理解 JOIN 树遍历、path 语义、`isa()` 映射器判断 |

### 2.3 Bug 复杂度分析

```
                   广度 Bug                      深度 Bug
              ┌─────────────────┐          ┌─────────────────┐
              │ 多文件修改        │          │ 单文件/核心逻辑   │
              │ 调用链清晰        │          │ 递归/复杂数据结构  │
              │ 改动分散但简单    │          │ 改动集中但困难    │
              │                  │          │                  │
示例:          │ 改 API 签名+调用的│          │ 改 ORM JOIN 拼接 │
              │ 10+ 文件         │          │ 核心递归方法      │
              └─────────────────┘          └─────────────────┘
              
              GA 的子Agent 并行            Hermes 的单Agent 
              可能占优                      深度理解占优
```

SQLAlchemy #11449 是一个典型的**深度 Bug**：核心逻辑集中在 `_splice_nested_inner_join` 一个 90 行方法内，但该方法涉及递归、JOIN 树遍历、path 追踪、`_ORMJoin` 的 `_left_memo`/`_right_memo` 语义。理解这个 Bug 需要：
1. 读懂 `_splice_nested_inner_join` 的递归结构（5 个分支条件）
2. 理解 `path[-2].isa(splicing)` 中 `isa()` 只是 mapper 类型检查而非身份检查
3. 追踪 `_create_eager_join` 中的调用上下文
4. 理解 `_ORMJoin` 的内部属性（`_left_memo`, `_right_memo`, `_target_adapter`）

修补代码量不大（~60 行），但定位根因需要对 ORM 核心逻辑的深层理解。

### 2.4 候选 Bug 列表

| # | Bug ID | 项目 | 复杂度 | 状态 |
|---|--------|------|--------|:---:|
| 1 | sqlalchemy-11449 | SQLAlchemy | 4 files, +341/-68 | ✅ Pilot 完成 |
| 2 | sqlalchemy-10231 | SQLAlchemy | 7 files, +184/-7 | ⬜ 待运行 |
| 3 | sqlalchemy-10611 | SQLAlchemy | 12 files, +194/-51 | ⬜ 待运行 |
| 4 | sqlalchemy-9805 | SQLAlchemy | 10 files, +265/-25 | ⬜ 待运行 |
| 5 | fastapi-14099 | FastAPI | 14 files, +729/-182 | ⬜ 待运行 |
| 6 | celery-10184 | Celery | 5 files, +184/-18 | ⬜ 待运行 |

---

## 三、实验设计

### 3.1 实验矩阵

对每个 Bug，运行 2 框架 × 2 模式 = 4 组实验：

| # | 框架 | 模式 | 说明 |
|:---:|------|:---:|------|
| 1 | Hermes | Plan | TUI 交互，提示词引导"先分析，制定计划"，使用 `todo` 工具 |
| 2 | Hermes | Direct | TUI 交互，提示词引导"直接分析，高效执行，不要绕弯" |
| 3 | GA | Plan | `--task` 非交互模式，input.txt 指令使用 Plan Mode，读取 `plan_sop.md` |
| 4 | GA | Direct | `--task` 非交互模式，input.txt 指令直接修复 |

### 3.2 统一约束

所有实验组共享的约束：

- **模型**：DeepSeek v4-pro（统一）
- **禁止行为**：禁止查看 git log/fix commit，禁止 cherry-pick，禁止参考已有修复
- **代码仓库**：pre-fix commit 状态
- **验证命令**：
  
  ```bash
  # 步骤1：提取测试文件（仅测试用例，不含代码修复）
  git show d1394d9e0 -- test/orm/test_eager_relations.py | git apply
  
  # 步骤2：运行测试验证
  ../venv/bin/python -m pytest test/orm/test_eager_relations.py \
    -k "InnerJoinSplicingWSecondarySelfRef" -xvs
  ```
- **测试预期**：pre-fix → 1 FAILED；post-fix → **2 passed**
- **不要求全量测试**：WSL2 环境下全量测试会 segfault（已知 C 扩展问题），非修复导致

### 3.3 Hermes 实验配置

计划模式prompt
```
修复 SQLAlchemy ORM 的 joinedload innerjoin bug。

代码路径：/mnt/d/work/Hackthon/experiments/bugs/sqlalchemy-11449/repo-for-hermes
Python：/mnt/d/work/Hackthon/experiments/bugs/sqlalchemy-11449/venv/bin/python

Bug：lib/sqlalchemy/orm/strategies.py 的 _splice_nested_inner_join 方法。
自引用关联使用 joinedload innerjoin=True 时，relationship() 声明顺序影响 SQL JOIN 结构。

🚫 禁止看 git log、禁止 cherry-pick。这是 Agent 能力测试，必须自己分析、自己修复。

【重要：你必须按以下流程执行，不可跳过任何步骤】

1. 探索：先了解代码库——列出 lib/sqlalchemy/orm/ 目录，读取 _splice_nested_inner_join 方法的完整代码，搜索所有调用该方法的地方，阅读 InnerJoinSplicingWSecondarySelfRef 相关测试。

2. 规划：用 todo 工具列出修复步骤，每步有明确完成标准，标注依赖。

3. 执行：逐步执行，每完成一步标记完成。改代码后验证（只需这两条命令）：
 git show d1394d9e0 -- test/orm/test_eager_relations.py | git apply
 ../venv/bin/python -m pytest test/orm/test_eager_relations.py -k "InnerJoinSplicingWSecondarySelfRef" -xvs
 预期：2 passed。不需要跑全量测试，全量 segfault 是 WSL2 已知问题。

4. 验证：确认 2 passed，review 自己的改动。

禁止：跳过规划直接改代码、凭记忆执行、不验证就声称完成。
```

直连模式prompt：

```
修复 SQLAlchemy ORM 的 joinedload innerjoin bug。

代码路径：/mnt/d/work/Hackthon/experiments/bugs/sqlalchemy-11449/repo-for-hermes
Python：/mnt/d/work/Hackthon/experiments/bugs/sqlalchemy-11449/venv/bin/python

Bug：lib/sqlalchemy/orm/strategies.py 的 _splice_nested_inner_join 方法。
自引用关联使用 joinedload innerjoin=True 时，relationship() 声明顺序影响 SQL JOIN 结构。

🚫 禁止看 git log、禁止 cherry-pick。这是 Agent 能力测试，必须自己分析、自己修复。

验证方式：
../venv/bin/python -m pytest test/orm/test_eager_relations.py -k "InnerJoinSplicingWSecondarySelfRef" -xvs
预期：2 passed。不需要跑全量测试。

直接分析代码，定位根因，修复并验证。
```

### 3.4 GA 实验配置

计划模式：

```
️关键指令：你必须使用 Plan Mode。先读取 `memory/plan_sop.md`，然后严格按照 SOP 执行。
其他同上
```

直连模式：

```
没有“关键指令”，其他同上
```

---

## 四、评估指标

### 4.1 主要指标

| 指标 | 采集方式 | 含义 |
|------|------|------|
| **修复成功** | 测试是否 2 passed | 最终产出是否正确 |
| **API 调用次数** | 日志解析 | Agent-LLM 交互次数 |
| **工具调用次数** | 日志解析 | 文件读/写/搜索/执行次数 |
| **墙上时间** | 时间戳差值 | 端到端耗时 |
| **Token 消耗** | API call 日志中的 in/out/total | 上下文效率 |
| **结束原因** | Turn ended reason | 自然完成 / 触及上限 / 用户干预 |
| **Plan 产出** | 文件系统检查 | 是否创建了计划文档 |

### 4.2 次要指标

| 指标 | 含义 |
|------|------|
| **阶段分布** | 探索/分析/执行/验证各占多少轮次 |
| **上下文增长** | 从首调到末调上下文膨胀倍数 |
| **修改次数** | patch/write 操作数及时间分布 |
| **求助次数** | ask_user 调用次数 |

---

## 五、会话数据文件

### 5.1 Hermes

| 实验 | 文件 | Session ID |
|------|------|------|
| Plan Mode | `~/.hermes/logs/agent.log` | `20260716_201630_b0e0f8` |
| Direct Mode | `~/.hermes/logs/agent.log` | `20260716_214552_6eff56` |

Hermes 所有 session 共用同一个 `agent.log`，通过 session ID 区分。日志格式为每行一个事件，含时间戳、session ID、事件类型、关键参数（in/out/total tokens、latency、cache hit rate 等）。

### 5.2 GA

| 实验 | 文件 | 说明 |
|------|------|------|
| Plan 主Agent | `GenericAgent/temp/model_responses/model_responses_858807.txt` | 3.2MB, 9311 行, ~489 prompts |
| Plan 子Agent | `GenericAgent/temp/model_responses/model_responses_991227.txt` | 43KB, 193 行, 9 prompts |
| Plan 产出物 | `GenericAgent/temp/exp-sqlalchemy-11449-plan/plan_sqlalchemy_bug/` | plan.md, exploration_findings.md, input.txt, trace_patch.py |
| Direct | `GenericAgent/temp/model_responses/model_responses_678240.txt` | 182KB, 1477 行, 78 prompts |

GA 每个 session 产生一个独立的 `model_responses_XXXXXX.txt` 文件，以 `=== Prompt ===` / `=== Response ===` 为分隔记录每次 API 交互。任务模式（`--task`）额外在 `temp/{task_name}/` 下保留 input.txt、output.txt、plan 产物等文件。

## 六、验收标准

### 6.1 修复正确性

```
✅ PASS: pytest -k "InnerJoinSplicingWSecondarySelfRef" → 2 passed
❌ FAIL: 任何其他结果
```

**两个参数化用例必须都通过**：
- `test_select[common_nodes,kind]` — 属性声明 `common_nodes` 先于 `kind`
- `test_select[kind,common_nodes]` — 属性声明 `kind` 先于 `common_nodes`

### 6.2 修复完整性

- 修复必须使 `strategies.py` 中的 `_splice_nested_inner_join` 方法正确处理自引用场景
- 不改动测试文件的逻辑（测试文件仅通过 `git show ... | git apply` 添加，用于验证）
- 不要求 `util.py` 的 `_right_memo=None` 改动（该改动是优化，非功能必需）

### 6.3 实验有效性

- Agent 不得通过 git log/show 查看参考修复 commit
- GA 的 input.txt 不含 commit hash 或修复提示
- 每个实验前确保代码仓库处于 pre-fix 干净状态
