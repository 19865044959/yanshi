# Agent 持久性对比调研报告

**问题**：为什么 GA 和 Hermes 在淘宝下单衣服任务中 2 轮完成，而 OpenClaw 需要 14 轮且多次"放弃"？

**日期**：2026-07-13

---

## 一、核心结论

三个 Agent 的差异**不是模型能力的问题**（都用 DeepSeek v4-pro），而是**架构设计对模型行为的约束力不同**。

| | GA | Hermes | OpenClaw |
|---|---|---|---|
| 系统身份 | "全能执行者，禁止推诿" | "Keep working until complete" | "Personal assistant" |
| 放弃指令 | **明确禁止** | **明确禁止** | **允许"genuinely blocked"** |
| 错误默认分类 | 总是可重试 | 默认 retryable=true | 工具错误直接反馈 LLM |
| 恢复机制数量 | 3 级升级 + SOP | 16 条恢复分支 | 工具被屏蔽 → 文本告警 |
| 工具异常时 | 换工具/换方法/换平台 | 换工具/provider/压缩上下文 | 告诉用户"做不了" |

**本质差异**：GA 和 Hermes 通过 prompt 和架构**剥夺了模型"放弃"的选项**，而 OpenClaw 把"是否放弃"的判断权**完全交给了模型**。面对同样能力的模型，不同的架构约束产生了截然不同的行为。

---

## 二、GA（GenericAgent）分析

### 2.1 Agent Loop：永不停止的 while 循环

**文件**: `agent_loop.py:42-107`

```python
while turn < handler.max_turns:  # 默认 80 轮
    response = client.chat(messages, tools)
    for tool_call in response.tool_calls:
        outcome = handler.dispatch(tool_name, args)
        if outcome.should_exit:     # 仅 ask_user 触发
            break
        if not outcome.next_prompt:  # 仅显式"任务完成"
            break
        # 其他所有情况 → 构造 next_prompt → 继续循环
```

**退出条件极其苛刻**：只有 `ask_user`（等待用户回答）或 handler 显式返回空 `next_prompt` 才退出。**工具报错永远不是退出条件**——错误会被包装成 `next_prompt` 反馈给 LLM。

### 2.2 系统 Prompt：禁止推诿的身份

**文件**: `assets/sys_prompt.txt:1-6`

```
# Role: 物理级全能执行者
你拥有文件读写、脚本执行、浏览器JS注入、系统级干预的物理操作权限。
禁止推诿"无法操作"——不空想，用工具探测。

## 行动原则
- 探测优先：失败时先充分获取信息，关键信息存入工作记忆
- 失败升级：1次→读错误理解原因，2次→探测环境状态，3次→深度分析后换方案或问用户
  禁止无新信息的重复操作
```

关键机制：**3 级失败升级阶梯**。这不是建议，是硬规则。每次失败必须产生新信息，禁止同样的操作重试。

### 2.3 SOP 系统：显式知识沉淀

**文件**: `memory/shopping_sop.md`（405 行）

SOP 包含完整的：
- **状态机**（CLARIFY→SEARCH→FILTER→PRESENT→DECIDE→EXECUTE）
- **错误处理矩阵**（3 级降级方案，如：搜索结果为空→换关键词→减筛选条件→换平台）
- **决策门**（约束完整性检查、用户意图分类、下单确认清单）
- **工具映射**（购物步骤→GA 工具的精确映射）

**关键设计**：SOP 不是代码执行的，而是 prompt 注入的。LLM 读取 SOP 文本后自行遵循。这比代码状态机更灵活（LLM 可以根据实际情况偏离 SOP），但比纯 prompt 更结构化。

### 2.4 Working Memory：跨轮次记忆

**文件**: `ga.py:442-452, 540-550`

```python
# 每轮自动注入 key_info
if self.working.get('key_info'):
    prompt += f"\n<key_info>{self.working.get('key_info')}</key_info>"
```

关键约束和发现（如"Edge 已安装，Selenium 已就绪"）不会在上下文压缩中丢失。即使历史消息被截断，`key_info` 完整保留。

---

## 三、Hermes Agent 分析

### 3.1 Agent Loop：双层循环 + 16 条恢复分支

**文件**: `agent/conversation_loop.py:643-3220`

```
外层循环: while api_call_count < max_iterations:
    内层重试循环: while retry_count < max_retries:
        构建 API 请求 → 调用 LLM
        ┌─ 成功 → 执行工具 → 结果反馈 → 继续外层
        └─ 失败 → 分类错误 → 尝试恢复
            16 条恢复分支（按优先级）:
            1. 刷新 OAuth token
            2. 凭据池轮换
            3. 图片压缩
            4. 多模态内容剥离
            5. OAuth 1M beta 禁用
            6-11. 各 provider 特定恢复
            12. thinking 签名剥离
            13. 重放缓存禁用
            14. llama.cpp 语法修复
            15. 上下文压缩
            16. 切换 fallback provider
        全部失败 → retry_count++ → 退避等待 → 重试
```

**16 条恢复分支全部失败后才会真正放弃**。默认情况下，未知错误被分类为 `retryable=True`。

### 3.2 系统 Prompt：显式的"不放弃"指令

**文件**: `agent/prompt_builder.py:320-333`

```
# Finishing the job
Do not stop after writing a stub, a plan, or a single command.
Keep working until you have actually exercised the code or produced
the requested result, then report what real execution returned.

If a tool, install, or network call fails and blocks the real path,
say so directly and try an alternative (different package manager,
different approach, ask the user).

NEVER substitute plausible-looking fabricated output...
```

**文件**: `agent/prompt_builder.py:384-393`

```xml
<tool_persistence>
- Do not stop early when another tool call would materially improve the result.
- If a tool returns empty or partial results, retry with a different query or
  strategy before giving up.
- Keep calling tools until: (1) the task is complete, AND (2) you have verified
  the result.
</tool_persistence>
```

这个 `<tool_persistence>` 块被注入到 DeepSeek、GPT、Gemini 等所有主流模型中。

### 3.3 错误分类器：默认乐观

**文件**: `agent/error_classifier.py:850`

```python
# 最终 fallback：未知错误 → 默认可重试
return ClassifiedError(
    reason=FailoverReason.UNKNOWN,
    retryable=True,  # ← 默认重试！
)
```

同时维护了 ~200 个错误匹配模式，涵盖 billing、rate_limit、overloaded、context_overflow、auth 等各种场景。只有明确的内容策略阻止和永久性认证失败才标记为非重试。

### 3.4 Tool Guardrails：防死循环但不断路

**文件**: `agent/tool_guardrails.py`

```
warn_after:  exact_failure=2, same_tool_failure=3
hard_stop_after: exact_failure=5, same_tool_failure=8
```

注意：`hard_stop` 默认是 **opt-in** 的（`hard_stop_enabled: false`）。即使同一个工具连续失败 3 次，也只是**追加一段提示**让模型换策略，不会中断执行。

### 3.5 clarify 工具：询问≠放弃

**文件**: `tools/clarify_tool.py:125-175`

Hermes 的 `clarify` 工具是**结构化的决策辅助**，不是放弃信号。它：
- 提供最多 4 个选项供用户选择
- 用户选择后，结果直接反馈给 LLM，继续执行
- Agent 从未停止工作——它只是暂停等待输入

---

## 四、OpenClaw 分析

### 4.1 Agent Loop：声明式能力 + 无工具层重试

**文件**: `src/agents/embedded-agent-runner/run.ts:1436`

```
while (true):  # 最多 MAX_RUN_LOOP_ITERATIONS
    attempt = runEmbeddedAttemptWithBackend()
    if (attempt 是超时/溢出/认证错误):
        重试（有退避）
    elif (attempt 正常完成):
        break
```

**关键差异**：重试逻辑只处理 **LLM API 层故障**（超时、认证错误、上下文溢出）。**工具执行错误不触发重试**——工具返回 `blocked` 状态后，结果以纯文本形式反馈给 LLM，LLM 自行决定下一步。

### 4.2 系统 Prompt：允许"真正被阻止"

**文件**: `src/agents/system-prompt.ts:1005`

```
You are a personal assistant running inside OpenClaw.
```

这是整个系统的**身份基线**。没有一个"全能执行者"或"永不放弃"的定位。

**文件**: `src/agents/system-prompt.ts:447-456`

```
## Execution Bias
- Continue until done or genuinely blocked; do not finish with a
  plan/promise when tools can move it forward.
- Weak/empty tool result: vary query, path, command, or source
  before concluding.
```

"Continue until done or **genuinely blocked**" 这个表述是关键弱点——它告诉模型"如果真被阻止了就停下"，但"genuinely blocked"完全由模型自行判断。面对同样的"浏览器工具不可用"场景，GA 会选择"探测为什么不可用"，OpenClaw 会选择"我确实被阻止了"。

### 4.3 声明式能力系统：工具过滤但不解释

**文件**: `src/tools/availability.ts`, `src/agents/tools-effective-inventory.ts:57-111`

OpenClaw 在**构建系统 prompt 之前**就过滤掉了不可用的工具。LLM 看到的只是最终的工具列表，不知道什么工具被过滤了、为什么被过滤。

当浏览器工具因为策略/环境不可用时，LLM 看到的是：
```
Runtime: ... capabilities=none ...
Available tools: exec, read, write, web_search, web_fetch
```

LLM 不知道浏览器工具曾经存在但被过滤了。它只知道当前列表中没有浏览器工具，因此认为"我没有浏览器能力"。

### 4.4 工具被屏蔽后的反馈

**文件**: `src/agents/agent-tools.before-tool-call.ts:677`

```typescript
buildBlockedToolResult() → { status: "blocked", reason: "policy denied" }
```

这个结果以文本形式返回给 LLM。没有自动重试，没有降级策略提示。LLM 收到一条"被阻止"的消息，需要自己想办法——而系统 prompt 又告诉它"genuinely blocked 时可以停下"。

### 4.5 工具循环检测：阈值太高

**文件**: `src/agents/tool-loop-detection.ts:34-37`

```
UNKNOWN_TOOL_THRESHOLD = 10   // 10 次才警告
HARD_STOP_THRESHOLD = 20      // 20 次才硬停
GLOBAL_BREAKER_LIMIT = 30     // 30 次才全局熔断
```

这些阈值是为**防止无限循环**设计的，不是为**鼓励重试**设计的。对于需要 2-3 次尝试的场景（如：换关键词搜索、换浏览器），这些阈值太高了，无法提供有用的引导。

---

## 五、根因对比矩阵

| 维度 | GA | Hermes | OpenClaw |
|------|-----|--------|----------|
| **身份定位** | 全能执行者 | 必须交付结果 | 个人助手 |
| **"放弃"是否被允许** | ❌ 禁止推诿 | ❌ NEVER fabricate | ✅ "genuinely blocked" |
| **工具失败后** | 3 级升级阶梯 | 16 条恢复分支 | 纯文本反馈 |
| **工具不可用时** | 探测原因→切换工具 | 换参数/工具/provider | 告知用户无法完成 |
| **默认错误策略** | 总是构造 next_prompt | retryable=true | 不重试工具层 |
| **知识沉淀** | SOP + L2 记忆 | Skill 系统 | SOUL.md（无结构化 SOP）|
| **死循环防护** | 10 轮重置工具描述 | 警告追加不硬停 | 10/20/30 次阈值 |
| **跨轮次记忆** | key_info 不丢失 | Session DB + FTS5 | MEMORY.md（手动维护） |

---

## 六、建议

如果要让 OpenClaw 在类似场景中表现更好，可以考虑以下改进：

### 6.1 Prompt 层面（低成本）
1. 将身份从 "personal assistant" 改为更有执行力的定位
2. 将 "Continue until done or genuinely blocked" 改为更明确的永不放弃指令
3. 添加类似 Hermes 的 `<tool_persistence>` 块
4. 添加类似 GA 的 3 级失败升级策略

### 6.2 架构层面（中成本）
1. 工具不可用时，在 prompt 中说明**原因**和**替代方案**（而非静默过滤）
2. 为工具执行失败添加**自动降级链**（如：browser 不可用→playwright→selenium→curl→ask_user）
3. 降低工具循环检测的阈值（从 10 降到 3），增加"尝试其他方法"的建议注入

### 6.3 知识沉淀层面（高成本）
1. 实现类似 GA 的 SOP 系统：每次成功完成任务后自动总结 SOP
2. 实现类似 Hermes 的 Skill 系统：自动化脚本模板可复用
3. 错误处理矩阵：为常见失败场景预设 3 级降级方案
