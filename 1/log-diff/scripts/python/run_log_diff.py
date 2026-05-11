#!/usr/bin/env python3
"""
run_log_diff.py - 日志对比分析工具

输入 UUID，从结构化日志中检索执行链路，对比基线输出异常报告。

用法:
    python run_log_diff.py <UUID> [--date YYYY-MM-DD] [--baseline PATH] [--save-baseline]

示例:
    python run_log_diff.py abc-123-def
    python run_log_diff.py abc-123-def --date 2026-05-09
    python run_log_diff.py abc-123-def --baseline logs/baseline/good.json
    python run_log_diff.py abc-123-def --save-baseline
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# ---- 常量 ----
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"
BASELINE_DIR = LOGS_DIR / "baseline"

LATENCY_THRESHOLD_MS = 3000.0  # 单步耗时告警阈值


# ---- 日志搜索 ----
def find_log_files(date: Optional[str] = None) -> list[Path]:
    """找到所有 JSONL 日志文件"""
    if not LOGS_DIR.is_dir():
        return []

    patterns = ["api_log_*.jsonl", "tool_log_*.jsonl"]
    if date:
        patterns = [f"api_log_{date}.jsonl", f"tool_log_{date}.jsonl"]

    files: list[Path] = []
    for pat in patterns:
        files.extend(sorted(LOGS_DIR.glob(pat)))
    return files


def search_uuid_in_jsonl(filepath: Path, uuid: str) -> list[dict]:
    """在单个 JSONL 文件中搜索包含指定 UUID 的行"""
    matches: list[dict] = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if uuid in line:
                    try:
                        entry = json.loads(line)
                        matches.append(entry)
                    except json.JSONDecodeError:
                        continue
    except (OSError, IOError):
        pass
    return matches


def search_all_logs(uuid: str, date: Optional[str] = None) -> list[dict]:
    """在所有日志文件中搜索 UUID，返回按时间排序的条目列表"""
    files = find_log_files(date)
    all_entries: list[dict] = []
    for fp in files:
        all_entries.extend(search_uuid_in_jsonl(fp, uuid))

    all_entries.sort(key=lambda e: e.get("ts", ""))
    return all_entries


# ---- 异常检测 ----
def detect_anomalies(entries: list[dict]) -> list[str]:
    """对每条日志条目做异常检测"""
    anomalies: list[str] = []
    for i, entry in enumerate(entries, 1):
        module = entry.get("module", "unknown")
        event = entry.get("event", "")
        success = entry.get("success")
        error = entry.get("error", "")
        latency = entry.get("latency_ms")

        # 错误事件
        if success is False or (error and error.strip()):
            anomalies.append(f"[步骤{i}] {module}/{event}: 执行失败 - {error or 'success=false'}")

        # 超时
        if latency is not None and isinstance(latency, (int, float)) and latency > LATENCY_THRESHOLD_MS:
            anomalies.append(
                f"[步骤{i}] {module}/{event}: 耗时 {latency:.1f}ms > 阈值 {LATENCY_THRESHOLD_MS:.0f}ms"
            )

    return anomalies


# ---- 链路构建 ----
def build_trace(entries: list[dict]) -> list[dict]:
    """将原始日志条目转为结构化 trace"""
    trace: list[dict] = []
    for i, entry in enumerate(entries, 1):
        latency = entry.get("latency_ms")
        success = entry.get("success")

        # 推断 success
        if success is None:
            err = entry.get("error", "")
            success = not bool(err and err.strip())

        step: dict[str, Any] = {
            "step": i,
            "ts": entry.get("ts", ""),
            "module": entry.get("module", "unknown"),
            "function": entry.get("function", ""),
            "event": entry.get("event", ""),
            "latency_ms": latency,
            "success": success,
        }

        # 摘要输入
        inp = entry.get("input")
        if isinstance(inp, dict):
            summary_keys = ["query", "method", "keywords", "timeout_s", "search_type"]
            summary = {k: inp[k] for k in summary_keys if k in inp}
            if summary:
                step["input_summary"] = summary

        # 摘要输出
        out = entry.get("output_summary")
        if isinstance(out, dict) and out:
            step["output_summary"] = out

        err = entry.get("error", "")
        if err:
            step["error"] = err

        trace.append(step)

    return trace


# ---- 基线对比 ----
def load_baseline(baseline_path: str) -> Optional[dict]:
    """加载基线文件"""
    bp = Path(baseline_path)
    if not bp.is_file():
        print(f"警告: 基线文件不存在: {baseline_path}", file=sys.stderr)
        return None
    try:
        with open(bp, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"警告: 无法读取基线文件: {e}", file=sys.stderr)
        return None


def compare_with_baseline(trace: list[dict], baseline: dict) -> dict:
    """与基线对比，返回 diff 结果"""
    baseline_trace: list[dict] = baseline.get("trace", [])
    baseline_modules = [s.get("module") for s in baseline_trace]
    current_modules = [s.get("module") for s in trace]

    diffs: list[str] = []

    # 步骤数量差异
    if len(trace) != len(baseline_trace):
        diffs.append(
            f"步骤数不一致: 当前 {len(trace)} vs 基线 {len(baseline_trace)}"
        )

    # 模块缺失
    for mod in baseline_modules:
        if mod not in current_modules:
            diffs.append(f"缺少模块: {mod}")

    # 模块多余
    for mod in current_modules:
        if mod not in baseline_modules:
            diffs.append(f"多了模块: {mod}")

    # 耗时对比
    for cur_step in trace:
        mod = cur_step.get("module")
        cur_lat = cur_step.get("latency_ms")
        if cur_lat is None:
            continue
        for bl_step in baseline_trace:
            if bl_step.get("module") == mod:
                bl_lat = bl_step.get("latency_ms")
                if bl_lat is not None and cur_lat > bl_lat * 1.5:
                    diffs.append(
                        f"{mod}: 耗时增加 {cur_lat - bl_lat:.0f}ms (基线 {bl_lat:.0f}ms)"
                    )
                break

    # 错误对比
    cur_errors = [s for s in trace if not s.get("success", True)]
    bl_errors = [s for s in baseline_trace if not s.get("success", True)]
    if len(cur_errors) > len(bl_errors):
        diffs.append(f"错误步骤增多: 当前 {len(cur_errors)} vs 基线 {len(bl_errors)}")

    return {
        "baseline": baseline.get("_source", "未知"),
        "diff_items": diffs,
        "diff_summary": "; ".join(diffs) if diffs else "与基线一致，无异常差异",
    }


def save_baseline(uuid: str, result: dict) -> str:
    """保存当前 trace 为基线"""
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    baseline_path = BASELINE_DIR / f"{uuid}.json"
    result["_source"] = str(baseline_path)
    result["_saved_at"] = datetime.now().isoformat()
    with open(baseline_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return str(baseline_path)


# ---- 主入口 ----
def main():
    parser = argparse.ArgumentParser(description="日志对比分析 - 输入UUID检索执行链路")
    parser.add_argument("uuid", help="要检索的请求 UUID")
    parser.add_argument("--date", default=None, help="限定日期，如 2026-05-09")
    parser.add_argument("--baseline", default=None, help="基线文件路径")
    parser.add_argument("--save-baseline", action="store_true", help="将本次结果保存为基线")
    parser.add_argument("--output", default=None, help="输出报告文件路径（默认 stdout）")
    args = parser.parse_args()

    uuid = args.uuid.strip()
    if not uuid:
        print(json.dumps({"success": False, "error": "UUID 不能为空"}, ensure_ascii=False))
        sys.exit(1)

    # 1. 搜索日志
    entries = search_all_logs(uuid, args.date)

    if not entries:
        result = {
            "success": False,
            "uuid": uuid,
            "error": f"未找到 UUID={uuid} 的日志记录。请确认 UUID 正确且日志中存在该字段。",
            "searched_dir": str(LOGS_DIR),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)

    # 2. 构建 trace
    trace = build_trace(entries)

    # 3. 异常检测
    anomalies = detect_anomalies(entries)

    # 4. 统计摘要
    total_latency = sum(
        e.get("latency_ms", 0.0) or 0.0 for e in entries
    )
    success_count = sum(1 for s in trace if s.get("success", True))
    error_count = len(trace) - success_count

    result: dict[str, Any] = {
        "success": True,
        "uuid": uuid,
        "search_date": args.date or "全部",
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_steps": len(trace),
            "success_steps": success_count,
            "error_steps": error_count,
            "total_latency_ms": round(total_latency, 1),
            "anomalies": anomalies if anomalies else [],
        },
        "trace": trace,
    }

    # 5. 基线对比
    baseline_path = args.baseline
    if baseline_path:
        baseline = load_baseline(baseline_path)
        if baseline:
            result["comparison"] = compare_with_baseline(trace, baseline)

    # 6. 保存基线
    if args.save_baseline:
        saved_path = save_baseline(uuid, result)
        result["baseline_saved"] = saved_path

    # 7. 输出
    output_json = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_json)
        print(f"报告已写入: {args.output}")
    else:
        print(output_json)


if __name__ == "__main__":
    main()
