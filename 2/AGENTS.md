# RustSysAgent — AI 协作开发规范

---

## 1. 分层结构化日志规范（强制执行）

### 1.1 核心原则

**每个工程模块的关键架构分层边界，必须埋入三类结构化日志：入口日志、出口日志、异常日志。每次代码执行强制落盘，为 log-diff 对比分析提供数据基础。**

```
┌─────────────────────────────────────────────────────────────────┐
│                    三层日志模型                                  │
│                                                                 │
│   ┌──────────┐     ┌──────────┐     ┌──────────┐               │
│   │ ENTRY    │ ──→ │ EXECUTE  │ ──→ │ EXIT     │               │
│   │ 入口日志  │     │          │     │ 出口日志  │               │
│   └──────────┘     └──────────┘     └──────────┘               │
│                          │                                      │
│                          ▼                                      │
│                     ┌──────────┐                                │
│                     │ EXCEPTION│  ← 任何时候出错都触发            │
│                     │ 异常日志  │                                │
│                     └──────────┘                                │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 统一日志格式（强制）

所有结构化日志必须使用以下 JSONL 格式，每行一条完整 JSON：

```json
{
  "ts": "2026-05-11T14:30:00.123456+08:00",
  "level": "INFO|WARN|ERROR",
  "layer": "gateway|message_bus|orchestrator|databus|plugin|node|agent_host",
  "module": "模块名",
  "function": "函数名",
  "event": "layer_entry|layer_exit|layer_error",
  "uuid": "请求UUID（从WebSocket Header透传）",
  "session_id": "会话ID",
  "duration_ms": 12.5,
  "input": { },
  "output": { },
  "error": "",
  "stack": ""
}
```

**字段强制规则**：

| 字段 | 入口日志 | 出口日志 | 异常日志 |
|------|:--------:|:--------:|:--------:|
| `ts` | ✓ 必填 | ✓ 必填 | ✓ 必填 |
| `level` | INFO | INFO | ERROR |
| `event` | `layer_entry` | `layer_exit` | `layer_error` |
| `uuid` | ✓ 必填 | ✓ 必填 | ✓ 必填 |
| `session_id` | ✓ 必填 | ✓ 必填 | ✓ 必填 |
| `duration_ms` | ✗ | ✓ 必填 | ✓ 必填 |
| `input` | ✓ 必填 | ✗ | ✓ 必填 |
| `output` | ✗ | ✓ 必填 | ✗ |
| `error` | ✗ | ✗ | ✓ 必填 |
| `stack` | ✗ | ✗ | ✓ 必填 |

### 1.3 uuid 透传铁律

**uuid 是日志对比的唯一关联键，必须在整个调用链路上不透传、不丢失。** 违反此条，log-diff 无法工作。

```
WebSocket请求 ──→ Gateway ──→ MessageBus ──→ Orchestrator ──→ Node
   uuid=a       uuid=a      uuid=a         uuid=a          uuid=a
                                                     │
                                           Node ──→ DataBus ──→ LLM Plugin
                                           uuid=a      uuid=a      uuid=a
```

**实现要求**：
- Rust 侧：`InternalMessage.payload.metadata` 中必须有 `ws_header_uuid`
- Python 侧：通过 `skill_sdk.ToolContext` 或环境变量 `RUST_SYS_AGENT_UUID` 获取
- Go 侧：通过 `node_sdk.NodeSDK.UUID()` 获取
- 任何新建模块的第一行代码就是接收并透传 uuid

---

## 2. 架构分层与日志埋点位置

### 2.1 RustSysAgent 架构分层图

```
┌──────────────────────────────────────────────────────────────┐
│  外部系统 (WebSocket / HTTP / CLI)                            │
└──────────────────────┬───────────────────────────────────────┘
                       │
              ┌────────┴────────┐
              │  L1: Gateway    │  ← 边界1: 外部→内部
              │  ws_gateway.rs  │
              │  http_gateway.rs│
              └────────┬────────┘
                       │ InternalMessage
              ┌────────┴────────┐
              │  L2: MessageBus │  ← 边界2: 消息入队/出队
              │  queue_manager  │
              │  event_bus      │
              └────────┬────────┘
                       │
              ┌────────┴────────┐
              │  L3: Orchestrator│ ← 边界3: 工作流调度
              │  workflow.rs    │
              │  executor.rs    │
              │  scheduler.rs   │
              └────────┬────────┘
                       │ spawn / UDS
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
  ┌──────────┐  ┌──────────┐  ┌──────────┐
  │L4: Node  │  │L4: Node  │  │L4: Node  │  ← 边界4: 节点进程
  │weather   │  │flight    │  │train     │
  └────┬─────┘  └────┬─────┘  └────┬─────┘
       │             │             │
       └─────────────┼─────────────┘
                     │
              ┌──────┴──────┐
              │ L5: DataBus │  ← 边界5: 数据共享
              │ L5: LLM     │  ← 边界5: 外部API调用
              └──────┬──────┘
                     │
              ┌──────┴──────┐
              │ L6: Outbound│  ← 边界6: 内部→外部
              │ ws_response │
              └─────────────┘
```

### 2.2 各层日志埋点清单

#### L1: Gateway 层

| 位置 | 文件 | 事件 | 输入 | 输出 |
|------|------|------|------|------|
| WebSocket 消息到达 | `framework/src/gateway/ws_gateway.rs` | `layer_entry` | WsRequest JSON | — |
| 协议转换完成 | `framework/src/gateway/ws_gateway.rs` | `layer_exit` | — | InternalMessage 摘要 |
| 消息解析失败 | `framework/src/gateway/ws_gateway.rs` | `layer_error` | 原始消息 | — |

```rust
// 示例：ws_gateway.rs handle_socket() 中添加
tracing::info!(
    layer = "gateway",
    module = "ws_gateway",
    event = "layer_entry",
    uuid = %request.Header.uuid,
    session_id = %session.id,
    input = %text,
    "WebSocket 消息到达"
);
```

#### L2: MessageBus 层

| 位置 | 文件 | 事件 | 输入 | 输出 |
|------|------|------|------|------|
| 消息推入输入队列 | `framework/src/message_bus/queue_manager.rs` | `layer_entry` | InternalMessage | — |
| 消息出队消费 | `framework/src/message_bus/queue_manager.rs` | `layer_exit` | — | InternalMessage |
| 队列满/阻塞 | `framework/src/message_bus/queue_manager.rs` | `layer_error` | 队列状态 | — |

#### L3: Orchestrator 层

| 位置 | 文件 | 事件 | 输入 | 输出 |
|------|------|------|------|------|
| 工作流开始执行 | `framework/src/orchestrator/executor.rs` | `layer_entry` | workflow_id + input | — |
| 节点调度决策 | `framework/src/orchestrator/scheduler.rs` | `layer_exit` | — | 可执行节点列表 |
| 工作流执行完成 | `framework/src/orchestrator/executor.rs` | `layer_exit` | — | 汇总结果 |
| 节点超时/失败 | `framework/src/orchestrator/executor.rs` | `layer_error` | 节点状态 | — |

#### L4: Node 层（跨语言）

| 语言 | 入口 | 出口 | 异常 |
|------|------|------|------|
| Python | `skill_sdk.ToolInstance.Invoke()` 入口 | `Invoke()` return | `Invoke()` exception |
| Go | `node_sdk.NodeSDK.OnInput()` 入口 | `SendOutput()` | `defer recover()` |
| Rust | `main()` stdin 收到 input | stdout 发送 output | 进程异常退出 |

**Python 节点日志示例**：

```python
import json, time, traceback, os

def _log_entry(uuid: str, session_id: str, module: str, func: str, input_data: dict):
    """入口日志 — 每次 Invoke 必须首先调用"""
    log_line = json.dumps({
        "ts": datetime.now().isoformat(),
        "level": "INFO",
        "layer": "node",
        "module": module,
        "function": func,
        "event": "layer_entry",
        "uuid": uuid,
        "session_id": session_id,
        "input": input_data
    }, ensure_ascii=False)
    print(log_line, file=sys.stderr)  # stderr 不干扰 stdout 协议
    # 同时写文件
    _append_log(log_line)

def _log_exit(uuid: str, session_id: str, module: str, func: str, 
              output_data: dict, duration_ms: float):
    """出口日志 — 每次 Invoke return 前必须调用"""
    log_line = json.dumps({
        "ts": datetime.now().isoformat(),
        "level": "INFO",
        "layer": "node",
        "module": module,
        "function": func,
        "event": "layer_exit",
        "uuid": uuid,
        "session_id": session_id,
        "duration_ms": round(duration_ms, 3),
        "output": output_data
    }, ensure_ascii=False)
    _append_log(log_line)

def _log_error(uuid: str, session_id: str, module: str, func: str,
               error_msg: str, input_data: dict, duration_ms: float):
    """异常日志 — 任何异常捕获后必须调用"""
    log_line = json.dumps({
        "ts": datetime.now().isoformat(),
        "level": "ERROR",
        "layer": "node",
        "module": module,
        "function": func,
        "event": "layer_error",
        "uuid": uuid,
        "session_id": session_id,
        "duration_ms": round(duration_ms, 3),
        "input": input_data,
        "error": error_msg,
        "stack": traceback.format_exc()
    }, ensure_ascii=False)
    _append_log(log_line)
```

#### L5: DataBus / LLM 插件层

| 位置 | 事件 | 输入 | 输出 |
|------|------|------|------|
| DataBus put/get 调用 | `layer_entry` / `layer_exit` | key + namespace | value / 状态 |
| LLM API 请求发送 | `layer_entry` | prompt + model | — |
| LLM API 响应到达 | `layer_exit` | — | tokens + 内容摘要 |
| API 超时/余额不足 | `layer_error` | 请求参数 | — |

#### L6: Outbound 层

| 位置 | 事件 | 输入 | 输出 |
|------|------|------|------|
| 响应推送 WebSocket | `layer_entry` | ResponseMessage | — |
| 推送完成 | `layer_exit` | — | 推送状态 |
| 连接断开 | `layer_error` | connection_id | — |

---

## 3. 日志文件落地规范

### 3.1 文件命名与存储

```
项目根目录/
├── logs/
│   ├── api_log_{YYYYMMDD}.jsonl      # API 调用日志（LLM、搜索引擎等外部调用）
│   ├── tool_log_{YYYYMMDD}.jsonl     # 工具/Skill 执行日志（节点内逻辑）
│   ├── layer_log_{YYYYMMDD}.jsonl    # 【新增】层级边界日志（入口/出口/异常）
│   └── baseline/                     # 基线归档（log-diff skill 使用）
│       └── {uuid}.json               # 按 UUID 存储的正常执行 trace
```

### 3.2 文件写入要求

1. **按天切分**：文件名含日期，每天一个文件
2. **追加写入**：以 append mode 打开，不覆盖历史
3. **即时刷盘**：每条日志写完后立即 `flush()`，进程崩溃不丢日志
4. **权限控制**：文件权限 `0644`，目录权限 `0755`
5. **大小监控**：单文件超过 500MB 时告警，人工介入归档

### 3.3 执行时强制保存

```python
# 每个 skill/node 的入口函数必须包含以下代码片段：

import atexit, sys

def _ensure_flush():
    """注册 atexit 钩子，进程退出时强制刷盘"""
    for handler in logging.getLogger().handlers:
        handler.flush()

atexit.register(_ensure_flush)

# 或者直接使用 Python logging 的 FileHandler
# 关键：不能依赖缓冲区，每次写日志必须落盘
```

---

## 4. 日志生成时机规则

### 4.1 必须生成日志的时机（强制）

| 时机 | 日志类型 | 触发条件 |
|------|----------|----------|
| 模块函数入口 | `layer_entry` | 每个 public 函数的第1行 |
| 模块函数出口 | `layer_exit` | 每个 return 语句前 |
| 异常捕获 | `layer_error` | 每个 catch/except 块内 |
| 跨进程通信 | `layer_entry` + `layer_exit` | UDS/RPC/HTTP 调用前后 |
| 外部 API 调用 | `layer_entry` + `layer_exit` | HTTP 请求发送前/响应后 |
| 超时事件 | `layer_error` | 任何超时触发时 |
| 降级/重试 | `layer_error` | 降级逻辑触发时 |

### 4.2 不需要生成日志的时机

- 纯内部计算（如字符串拼接、数据格式转换）
- getter/setter 等 trivial 方法
- 日志模块自身的内部调用（避免无限递归）

---

## 5. 与 log-diff skill 的协作

### 5.1 协作流程

```
┌──────────────────────────────────────────────────────────────────┐
│                     开发阶段 (AI 干的活)                          │
│                                                                  │
│  1. AI 扫描工程架构，识别分层边界                                  │
│  2. AI 在每个边界埋入 entry/exit/error 日志                       │
│  3. AI 确保 uuid 全链路透传                                       │
│  4. AI 确保日志强制落盘                                           │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                     运行阶段 (生产环境)                            │
│                                                                  │
│  5. 系统正常运行，积累 layer_log_{date}.jsonl                     │
│  6. 将一次"正常"执行的 trace 保存为基线                           │
│     python scripts/python/run_log_diff.py <UUID> --save-baseline │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                     分析阶段 (运维使用)                            │
│                                                                  │
│  7. 出现异常时，输入 UUID 对比分析                                 │
│     python scripts/python/run_log_diff.py <UUID> --baseline ...  │
│  8. 自动定位：哪一层、哪个模块、哪个函数、慢了多少                  │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### 5.2 日志完整性检查

log-diff skill 执行时会自动检查：

1. **uuid 连续性**：trace 中每个 step 的 uuid 是否一致
2. **层次覆盖**：是否覆盖了所有架构层（L1~L6）
3. **事件配对**：每个 `layer_entry` 是否有对应的 `layer_exit` 或 `layer_error`
4. **耗时合理性**：`layer_exit.duration_ms` 是否与 `ts` 差值一致

---

## 6. AI 实施指南

### 6.1 接到"添加结构化日志"任务时的执行步骤

**Step 1 — 架构分析**：
- 阅读 `docs/arch.md` 了解架构分层
- 扫描源代码目录，识别模块边界
- 输出一份"日志埋点清单"供人工确认

**Step 2 — 格式对齐**：
- 确认使用 1.2 节定义的统一 JSONL 格式
- 确认 uuid 字段可以获取到（追溯调用链上游）
- 如果 uuid 不可用，先实现 uuid 透传，再做日志

**Step 3 — 逐层埋点**：
- 按照 2.2 节的清单，从上到下（L1→L6）逐层添加
- 每改完一层，编译/运行验证一次
- 优先改 Rust 框架层（因为所有流量经过），再改各语言节点

**Step 4 — 验证**：
- 发送一条测试请求，检查 `logs/layer_log_{date}.jsonl` 是否有完整链路
- 用 `run_log_diff.py <UUID>` 检索，确认能还原完整 trace
- 保存基线：`run_log_diff.py <UUID> --save-baseline`

### 6.2 新建模块时的强制要求

任何新建的模块/节点/skill，**必须在代码中包含**：

```rust
// Rust 模块模板
pub fn new_module_entry(uuid: &str, session_id: &str, input: &Input) -> Result<Output> {
    // 1. 入口日志
    log_layer_entry("layer", "module", "function", uuid, session_id, input);
    let start = Instant::now();

    // 2. 业务逻辑
    let result = match do_business_logic(input) {
        Ok(output) => output,
        Err(e) => {
            // 3. 异常日志
            log_layer_error("layer", "module", "function", uuid, session_id,
                          &e.to_string(), input, start.elapsed());
            return Err(e);
        }
    };

    // 4. 出口日志
    log_layer_exit("layer", "module", "function", uuid, session_id,
                 &result, start.elapsed());
    Ok(result)
}
```

### 6.3 日志量控制

为避免日志爆炸，遵循以下规则：

| 数据项 | 截断规则 |
|--------|----------|
| `input` / `output` 中的文本字段 | 超过 500 字符截断，末尾加 `...[truncated]` |
| `stack` | 保留前 10 行 |
| `response_body` | **不写入 layer_log**，仅写入 api_log |
| 二进制数据 | Base64 编码后截断到 200 字符 |

---

## 7. 编码规范

1. 命名空间、类类型、结构体类型、枚举类型 使用 **大驼峰**
2. 类成员变量、局部变量、函数参数 使用 **小驼峰**
3. 枚举值、常量 使用 **全大写+下划线分割**
4. 文件夹命名采用小写下划线，文件命名采用大驼峰或 snake_case（按语言惯例）
5. 每个源代码文件行数不超过 500 行（含注释和空行）
6. 实现代码中必须有详细的中文注释

---

## 8. 与 log-diff skill 的配套使用

### 快速开始

```bash
# 1. 确认日志正在生成
tail -f logs/layer_log_$(date +%Y%m%d).jsonl

# 2. 发送一条测试请求（记录其 UUID）
# WebSocket response Header 中取 UUID

# 3. 用 UUID 检索完整链路
cd agents/skills/log-diff
python scripts/python/run_log_diff.py <UUID>

# 4. 将正常链路保存为基线
python scripts/python/run_log_diff.py <UUID> --save-baseline

# 5. 后续每次对比
python scripts/python/run_log_diff.py <NEW_UUID> \
    --baseline logs/baseline/<UUID>.json
```

### 演示输出参考

参见 `agents/skills/log-diff/demo_output.txt`，展示了一次完整对比分析的全部输出内容。
