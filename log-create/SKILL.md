---
name: log-create
description: "创建结构化日志：在分层架构边界自动添加 entry/exit/error 三层日志，确保 uuid 全链路透传，日志强制落盘。Use for: 加日志、添加结构化日志、埋日志、日志规范、加埋点。Triggers: 加日志、加埋点、创建日志、log-create、添加入口日志、添加出口日志、日志落盘"
---

# 结构化日志埋点

在 RustSysAgent 分层架构的关键边界，按三层模型（entry/exit/error）埋入结构化日志，确保每次请求可完整追踪。

## 1. 三层日志模型

每个模块的 public 函数/边界必须产出三类日志：

```
ENTRY → 函数被调用时立刻打印（第1行）
EXIT  → 每个 return 语句前打印
ERROR → 每个 catch/except 块内打印
```

## 2. 统一 JSONL 格式

每行一条完整 JSON，写入文件（不是 stdout）：

```json
{
  "ts": "2026-05-11T14:30:00.123456+08:00",
  "level": "INFO|ERROR",
  "layer": "gateway|message_bus|orchestrator|databus|plugin|node|agent_host",
  "module": "模块名",
  "function": "函数名",
  "event": "layer_entry|layer_exit|layer_error",
  "uuid": "请求UUID（从上游透传）",
  "session_id": "会话ID",
  "duration_ms": 12.5,
  "input": {},
  "output": {},
  "error": "",
  "stack": ""
}
```

**字段必填规则**：

| 字段 | ENTRY | EXIT | ERROR |
|------|:-----:|:----:|:-----:|
| ts | ✓ | ✓ | ✓ |
| level | INFO | INFO | ERROR |
| event | layer_entry | layer_exit | layer_error |
| uuid | ✓ | ✓ | ✓ |
| session_id | ✓ | ✓ | ✓ |
| duration_ms | ✗ | ✓ | ✓ |
| input | ✓ | ✗ | ✓ |
| output | ✗ | ✓ | ✗ |
| error | ✗ | ✗ | ✓ |
| stack | ✗ | ✗ | ✓ |

## 3. uuid 透传铁律

uuid 是 log-diff 对比的唯一关联键，**必须在全链路不透传不丢失**：

```
WebSocket → Gateway → MessageBus → Orchestrator → Node → DataBus → LLM
   uuid       uuid       uuid          uuid        uuid     uuid     uuid
```

- Rust: `InternalMessage.payload.metadata` 中取 `ws_header_uuid`
- Python: `skill_sdk.ToolContext` 或环境变量 `RUST_SYS_AGENT_UUID`
- Go: `node_sdk.NodeSDK.UUID()`
- 新模块第一行代码就是接收并透传 uuid

## 4. 架构分层与埋点位置

参考 `docs/arch.md` 了解完整架构。六层边界以及各自的埋点位置：

### L1 Gateway
- ws_gateway / http_gateway：消息到达时 entry，协议转换完成后 exit，解析失败时 error

### L2 MessageBus
- queue_manager：消息入队 entry，消息出队消费 exit，队列满/阻塞 error

### L3 Orchestrator
- executor / scheduler：工作流开始 entry，调度决策/执行完成 exit，节点超时/失败 error

### L4 Node（跨语言）
- Python: `Invoke()` 入口 entry，return 前 exit，exception 时 error
- Go: `OnInput()` 入口 entry，`SendOutput()` 前 exit，`defer recover()` 中 error
- Rust: stdin 收到 input entry，stdout 发送 output exit，进程异常 error

### L5 DataBus / LLM Plugin
- DataBus put/get 调用前后 entry/exit
- LLM API 请求发送前 entry，响应到达后 exit，超时/错误 error

### L6 Outbound
- 响应推送 WebSocket entry，推送完成 exit，连接断开 error

## 5. 日志落盘

- 文件路径：`logs/layer_log_{YYYYMMDD}.jsonl`
- 按天切分，追加写入，每条 flush
- 文件权限 0644，目录 0755
- 单文件超 500MB 告警

## 6. 数据截断规则

| 数据项 | 规则 |
|--------|------|
| input/output 文本 | 超 500 字符截断，末尾加 `...[truncated]` |
| stack | 保留前 10 行 |
| 二进制数据 | Base64 后截断到 200 字符 |

## 7. 代码模板

### Rust

```rust
pub fn module_entry(uuid: &str, session_id: &str, input: &Input) -> Result<Output> {
    log_layer_entry("layer_name", "module", "function", uuid, session_id, input);
    let start = Instant::now();

    let result = match do_business_logic(input) {
        Ok(output) => output,
        Err(e) => {
            log_layer_error("layer_name", "module", "function", uuid, session_id,
                          &e.to_string(), input, start.elapsed());
            return Err(e);
        }
    };

    log_layer_exit("layer_name", "module", "function", uuid, session_id,
                 &result, start.elapsed());
    Ok(result)
}
```

### Python

```python
import json, time, traceback, sys
from datetime import datetime

def _write_log(log_obj: dict):
    line = json.dumps(log_obj, ensure_ascii=False) + "\n"
    with open(f"logs/layer_log_{datetime.now().strftime('%Y%m%d')}.jsonl", "a") as f:
        f.write(line)
        f.flush()

def log_entry(uuid: str, session_id: str, layer: str, module: str,
              func: str, input_data: dict):
    _write_log({
        "ts": datetime.now().isoformat(),
        "level": "INFO", "layer": layer, "module": module,
        "function": func, "event": "layer_entry",
        "uuid": uuid, "session_id": session_id,
        "input": input_data
    })

def log_exit(uuid: str, session_id: str, layer: str, module: str,
             func: str, output_data: dict, duration_ms: float):
    _write_log({
        "ts": datetime.now().isoformat(),
        "level": "INFO", "layer": layer, "module": module,
        "function": func, "event": "layer_exit",
        "uuid": uuid, "session_id": session_id,
        "duration_ms": round(duration_ms, 3),
        "output": output_data
    })

def log_error(uuid: str, session_id: str, layer: str, module: str,
              func: str, error_msg: str, input_data: dict, duration_ms: float):
    _write_log({
        "ts": datetime.now().isoformat(),
        "level": "ERROR", "layer": layer, "module": module,
        "function": func, "event": "layer_error",
        "uuid": uuid, "session_id": session_id,
        "duration_ms": round(duration_ms, 3),
        "input": input_data, "error": error_msg,
        "stack": traceback.format_exc()
    })
```

## 8. 执行步骤

当用户要求添加结构化日志时，按以下步骤执行：

1. **确认范围**：问清是给哪个模块/文件加日志。未指定时，扫描近期改动的模块
2. **确认 uuid 可达**：检查目标模块能否拿到 uuid。不能则先实现 uuid 透传
3. **逐函数埋点**：每个 public 函数按模板添加 entry/exit/error
4. **验证**：改完后编译检查，确认格式符合第二节的字段规则

## 9. 不需要加日志的情况

- 纯内部计算（字符串拼接、格式转换）
- getter/setter 等简单访问器
- 日志模块自身的内部调用
