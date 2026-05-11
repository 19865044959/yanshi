---
name: log-diff
description: "日志对比分析：输入一个请求UUID，从结构化日志中检索该次请求的完整执行链路，对比基线输出异常报告。Use for: 日志对比、异常排查、请求追踪、性能分析。Triggers: 日志对比、查日志、分析请求、对比日志、diff日志、异常分析"
---

# 日志对比分析

根据输入 **UUID**，在 `logs/` 目录下的结构化日志（`api_log_*.jsonl`、`tool_log_*.jsonl`）中检索该次请求的完整执行链路，生成对比分析报告。

## 1. 输入

- **UUID**（必填）：WebSocket Header 中的 UUID，唯一标识一次请求
- **--baseline**（可选）：指定基线日志文件路径，默认使用 `logs/baseline/` 下最近一次归档
- **--date**（可选）：限定搜索日期，如 `2026-05-09`，默认搜索所有日期

## 2. 输出

成功时输出 JSON 格式的分析报告：

```json
{
  "success": true,
  "uuid": "xxx",
  "summary": {
    "total_steps": 5,
    "success_steps": 4,
    "error_steps": 1,
    "total_latency_ms": 4500.0,
    "anomalies": ["weather_query 超时 3200ms > 阈值 3000ms"]
  },
  "trace": [
    {
      "step": 1,
      "ts": "2026-05-09T11:00:07.374",
      "module": "aggregate_search",
      "event": "api_request",
      "latency_ms": 2706.5,
      "success": true,
      "anomaly": null
    }
  ],
  "comparison": {
    "baseline": "logs/baseline/api_log_20260501.jsonl",
    "diff_summary": "比基线多1个步骤，weather_query 耗时增加 500ms"
  }
}
```

## 3. 异常判定规则

| 规则 | 阈值 |
|------|------|
| 单步耗时超限 | > 3000ms |
| 错误事件 | success=false 或 error 字段非空 |
| 步骤缺失 | 对比基线，缺少某 module 的执行记录 |
| 步骤多余 | 对比基线，多了某 module 的执行记录 |
| 步骤乱序 | 执行顺序与基线不一致 |

## 4. 基线管理

- 基线是"好的日志"归档，运行本 skill 时可指定 `--save-baseline` 将当前请求的 trace 保存为基线
- 基线文件存储在 `logs/baseline/` 目录下，按 UUID 命名

## 5. 执行

调用入口 **`scripts/python/run_log_diff.py`**：

```bash
# 基本用法：输入 UUID 查日志
python scripts/python/run_log_diff.py <UUID>

# 指定日期范围
python scripts/python/run_log_diff.py <UUID> --date 2026-05-09

# 对比指定基线
python scripts/python/run_log_diff.py <UUID> --baseline logs/baseline/good_trace.json

# 保存当前 trace 为基线
python scripts/python/run_log_diff.py <UUID> --save-baseline
```

## 6. 触发场景

当用户表达以下意图时使用本 skill：日志对比、查日志、分析某次请求、排查异常、对比基线、diff 日志、请求追踪、性能分析。
