# Plan Mode vs Direct Mode：三框架对比报告

**实验日期**：2026-07-16 ~ 2026-07-17  
**Bug**：SQLAlchemy #11449 — `joinedload.innerjoin` 自引用关联 JOIN 拼接错误（深度Bug，核心文件 ~400行关键逻辑）  
**模型**：DeepSeek v4-pro（全部实验统一）  
**实验组**：Hermes Plan / Hermes Direct / GA Plan / GA Direct  

---

## 一、结果总览

| | Hermes Plan | Hermes Direct | GA Plan | GA Direct |
|---|:---:|:---:|:---:|:---:|
| **修复成功** | ✅ | ❌ | ❌ | ❌ |
| **耗时** | 12.5 min | 22.2 min | 57 min | 6 min |
| **API 调用** | 55 | 90（触及上限） | ~498 | 78 |
| **Turns** | 52 | 90 | ~80 | 77 |
| **结束原因** | 自然完成 | max_iterations | 分析瘫痪 | ask_user 放弃 |
| **代码修改** | 6 patches | 9 patches + 4 writes | 52 file_patch | 3 file_patch |
| **Plan 计划** | todo（内存，5次） | 无 | plan.md（文件，无checkbox） | 无 |

---

## 二、Hermes vs GA：为什么一个成功一个失败？

### 2.1 Prompt 引导：两种根本不同的行动哲学

**GA 的系统提示词（6行）**：

```
探测优先：失败时先充分获取信息
失败升级：1次→读错误理解原因，2次→探测环境状态，3次→深度分析后换方案
```

关键词：**"失败时"**。探测被定义为失败后的补救，不是行动前的准备。引导的行为模式是：

```
动手 → 失败 → 探测 → 再动手 → 再失败 → 再探测 → ...
```

**Hermes 的系统提示词（~570行多层组装）**：

```
DEFAULT_AGENT_IDENTITY:
  "Be targeted and efficient in your exploration and investigations."

OPENAI_MODEL_EXECUTION_GUIDANCE:
  "Keep calling tools until: (1) the task is complete, AND (2) you have verified."

PARALLEL_TOOL_CALL_GUIDANCE:
  "Independent reads, searches should be batched into the same assistant turn."
```

引导的行为模式是：

```
探索 → 理解 → 规划 → 执行 → 验证 → 完成
```

**两种引导在不同场景下的适用性**：

| 场景 | GA（先动手） | Hermes（先理解） |
|------|:---:|:---:|
| 简单任务（1-2文件，逻辑直白）| ✅ 快 | ✅ 也行 |
| 中等任务（3-5文件，有调用链）| ⚠️ 可能 | ✅ |
| 深度任务（单文件，逻辑复杂/递归）| ❌ 陷入死胡同 | ✅ |
| 广度任务（多文件并行扫描）| ⚠️ | ✅（并行读取） |

这个 Bug 是深度任务——核心逻辑在 `_splice_nested_inner_join` 一个 90 行方法里，但它涉及递归、JOIN 树遍历、path 语义。GA 的"先动手"模式导致 Agent 在没有全貌理解的情况下就开始 patch → test fail → trace → patch → fail → 换方式 trace 的循环。GA Plan 和 GA Direct **都陷入了这个循环**，区别只是 Plan 多花了 9.5× 的 API 调用在框架开销上。

### 2.2 框架设计：子Agent 上下文断裂

GA 的 Plan Mode（`plan_sop.md` 263行）强制主Agent 不能直接探测代码，必须委托子Agent：

```
plan_sop.md L14:
⛔ 主agent禁止直接执行环境探测（必须委托subagent，无例外）
```

**实际发生的情况**（有日志为证）：

```
主Agent的 key_info:
  [路径] 仓库: /mnt/d/work/Hackthon/experiments/bugs/sqlalchemy-11449/repo  ← 有路径

子Agent收到的 input.txt:
  "探测 strategies.py 的 _splice_nested_inner_join 方法和 
   InnerJoinSplicingWSecondarySelfRef 测试用例"              ← 无路径

子Agent的第一步:
  file_read /mnt/d/work/Hackthon/GenericAgent/temp/strategies.py
  → Error: File not found                                   ← 路径丢失

子Agent的第四步:
  ask_user "请确认是哪个 strategies.py？"                    ← 被迫求助

主Agent的三次干预:
  L236: 写 _keyinfo 纠正  → "subagent路径错了"
  L331: 写 _intervene 纠正 → "subagent 卡在 ask_user 了"  
  L388: 写 reply.txt 纠正  → "让我直接写入 reply.txt"
```

从启动子Agent（21:18:31）到子Agent用上正确路径（21:19:41），**耗费 70 秒 + 主Agent 3 次手动干预 + 约 15 轮 API 调用**。这些轮次对 Bug 修复没有任何贡献。

**为什么这个 Bug 不适合子Agent？** 这是一个**深度任务**，不是**广度任务**。需要理解的是 ~400 行核心代码的逻辑和调用关系，而不是扫描大量文件。子Agent 的定位（`subagent.md`）是"并行处理 N 个独立文件"——它适用于广度，不适用于深度。但 GA 的 Plan SOP **把"必须委托子Agent"作为硬规则，不区分任务类型**。

Hermes 没有这个问题——Agent 自己直接读代码，理解是连续的、累积的，没有上下文断裂。

### 2.3 工具设计：并行 vs 串行

Hermes 支持在一次 API 调用中并行发起多个独立的 `read_file`：

```
Hermes Plan 的一次典型并行调用：
  API #4: read_file(strategies.py, L2548, 100行) ─┐
          read_file(test.py, L3700, 100行)        ─┤ 同一轮
          read_file(strategies.py, L2500, 50行)    ─┘
```

GA 必须串行：每个 `file_read` 占一轮，读到结果后才能请求下一个。

**效果对比**：

| | Hermes Plan | GA Plan |
|------|:---:|:---:|
| 建立代码全局视图 | 18 次 read_file | 154 次 file_read |
| 探索阶段 API 调用 | ~12 轮 | ~150+ 轮 |
| 上下文增长 | 36K → 136K (3.7×) | 持续膨胀 |

### 2.4 上下文压缩

Hermes 在上下文达到阈值时自动压缩，压缩后 `TodoStore.format_for_injection()` 将当前任务列表重新注入：

```
[Your active task list was preserved across context compression]
- [>] 1. 探索代码结构 (in_progress)
- [ ] 2. 分析根因 (pending)
- [ ] 3. 实施修复 (pending)
- [ ] 4. 验证测试 (pending)
```

GA 没有上下文压缩机制。在 57 分钟的 Plan 会话中，早期分析被淹没在 498 次 API 调用的历史中。

---

## 三、Hermes Plan vs Hermes Direct：同一个 Agent，为什么结果不同？

### 3.1 工具使用对比

| | Plan Mode | Direct Mode | 比值 |
|------|:---:|:---:|:---:|
| todo（任务管理） | 5 | 0 | — |
| read_file | 18 | 33 | 1.8× |
| search_files | 7 | 23 | **3.3×** |
| terminal | 8 | 15 | 1.9× |
| patch/write | 6 | 13 | 2.2× |
| 上下文增长 | 3.7× | 4.6× | — |

### 3.2 行为模式差异

**Plan Mode — 第一步就是 `todo`**：

```
API #1:   todo（建任务列表）← 结构化
API #2-3: 思考（330、240 tokens 深度分析）
API #4:   read_file × N（1630 tokens 输出，并行批量读取）
API #5-8: 交替阅读和思考
API #9:   深度分析（1196 tokens 输出）
...
API #33:  第一个 patch ← 已经读了 32 轮代码才动手
```

**Direct Mode — 第一步就是 `read_file + terminal`**：

```
API #1:   read_file + terminal ← 立刻动手
API #2-3: 快速思考（320、230 tokens）
API #4:   search_files（870 tokens）
API #5-15: 碎片化交替 read_file/search_files
           每步 out < 350 tokens ← 浅思考
...
API #?:   patch → terminal → patch → terminal → ... 分散在 15 分钟内
```

### 3.3 原因分析

Hermes 的系统提示词包含两股力量：

| 力量 | 内容 | 方向 |
|------|------|:---:|
| A | "Be targeted and efficient in your exploration" | 先理解 |
| B | "MUST use your tools to take action — execute it now" | 立刻行动 |

**力量B（立刻行动）非常强硬**："Responses that only describe intentions without acting are not acceptable."——不要描述意图，必须行动。

在没有 Plan 指令时，**力量B 占主导**——Agent 每轮必须产出工具调用，形成了"read → search → patch → terminal → read → ..."的碎片化节奏。

Plan Mode 的 prompt 强化了力量A，同时 `todo` 工具给了 Agent 一个合法的"非行动"出口——建任务列表也是工具调用，满足了力量B的"必须有 action"的要求，但把方向拉回了"先理解"。

**Plan Mode 不改变 Agent 的能力，但改变了它分配注意力的方式**：

```
Direct Mode 的注意力分配：          Plan Mode 的注意力分配：
  读代码   ████████ 35%               读代码   ████████████████ 55%
  搜索     ██████ 25%                 搜索     ████ 13%
  改代码   ██████████ 40%             改代码   ████ 14%
                                      思考     █████ 17%（todo + 分析）
```

---

## 四、GA Plan vs GA Direct：为什么 Plan 没有改变结果？

### 4.1 Plan 流程走了，但产出不可执行

GA Plan **确实**遵循了 SOP 的前半段：

```
✅ 读 plan_sop.md
✅ 创建 plan_sqlalchemy_bug/ 目录
✅ 启动探索子Agent（虽然断裂了）
✅ 写 plan.md（3192 bytes）
```

但 `plan.md` 的内容是一篇**技术分析文章**，不是**执行计划**：

```markdown
# plan.md 实际内容
## 问题分析
### 根因
_splice_nested_inner_join 在自引用场景下...路径追踪...isa()语义...

## 修复方案
方案A（推荐）：在 isa 检查基础上增加身份检查...
```

SOP 模板要求的是：

```markdown
## 执行计划
1. [ ] 步骤1简述
   SOP: xxx_sop.md
2. [D] 步骤2简述（委托subagent）
   依赖：1
```

**没有 `[ ]` checkbox。没有步骤。没有依赖关系。** 执行协议 `file_read(plan.md) → 找第一个[ ] → 执行 → 标记[✓]` 面对这份 plan 无东西可执行。

### 4.2 计划之后直接回退到默认模式

```
Turn 25: plan.md 写入 ✅
Turn 26: file_read
Turn 27: file_write trace_patch.py  ← 开始写调试脚本
Turn 30: code_run（复现bug）        ← 进入调试模式
Turn 34: Agent 说 "直接基于代码分析直接修复更高效"
         ↑ 主动放弃 Plan 流程
```

从此进入与 GA Direct 相同的 debug 循环，持续 52 轮直到放弃。

### 4.3 为什么 Hermes 不会产出不可执行的计划？

`todo` 工具的 schema 约束：

```json
{"id": "1", "content": "探索代码结构", "status": "pending"}
```

**不可能在 todo 里写技术分析。** 字段 `content` 就是任务描述，`status` 就是状态。工具的类型约束保证了产出质量。

`file_write` 写 `plan.md` 没有任何 schema 约束——Agent 可以写任何东西，包括一篇更适合作为博客而非执行计划的文章。

---

## 五、结论

### 5.1 实验结论

| 排名 | 方案 | 结果 | 决定性因素 |
|:---:|------|:---:|------|
| 1 | Hermes Plan | ✅ 成功 | Prompt引导"先理解" + 单Agent无断裂 + todo结构化 + 并行读取 |
| 2 | Hermes Direct | ❌ 90轮耗尽 | "立刻行动"主导 → 碎片化 → 上下文膨胀 |
| 3 | GA Direct | ❌ 77轮放弃 | "失败时探测"引导 → 调试死循环 → 求助 |
| 4 | GA Plan | ❌ 57min瘫痪 | 子Agent断裂 + plan不可执行 + 回退到默认模式 |

### 5.2 设计启示

1. **Prompt 引导决定行为基线**："失败时探测"（GA）vs "先理解再动手"（Hermes）——在深度任务上后者明显占优
2. **对深度任务，单Agent > 多Agent**：子Agent 适用于广度并行，不适用于需要深度理解单一复杂逻辑的场景
3. **结构化工具 > 自由文本**：`todo`（schema约束）产出的计划比 `plan.md`（自由文本）更可靠
4. **并行工具调用是倍增器**：一次 API 调用读 3 个文件 vs 3 轮 API 调用——累积差距巨大
5. **上下文压缩保持目标不丢失**：长对话中早期发现被淹没是真实问题，压缩+重新注入是解决方案

### 5.3 后续方向

- 5 个候选 Bug 的进一步验证（统计显著性）
- Claude Code 加入对比（JSONL 结构化日志天然支持）
- "Hermes 的 todo + GA 的文件持久化"混合方案探索

---

## 附录：实验数据

| 指标 | Her. Plan | Her. Direct | GA Direct | GA Plan |
|------|:---:|:---:|:---:|:---:|
| API 调用 | 55 | 90（上限） | 78 | ~498 |
| 墙上时间 | 12.5 min | 22.2 min | 6 min | 57 min |
| read_file | 18 | 33 | 26 | 154 |
| code_run/terminal | 8 | 15 | 45 | 242 |
| search_files | 7 | 23 | 0 | 0 |
| patch/write | 6 | 13 | 4 | 82 |
| todo | 5 | 0 | 0 | 0 |
| 始上下文 | 36K | 36K | — | — |
| 末上下文 | 136K | 165K | — | — |
| 并行读取 | ✅ | ✅ | ❌ | ❌ |
| 上下文压缩 | ✅ | ✅ | ❌ | ❌ |
| 修复成功 | ✅ | ❌ | ❌ | ❌ |
