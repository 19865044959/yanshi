# AgentFlow 三层架构重构方案

## Context

当前 V3C 架构问题：Go SDK 重度依赖 cgo；LLM/Skill/Tool/Context 逻辑在 C++/Rust/Go 中重复实现；AgentFlow 底座只提供 DataBus，其他能力未以服务形式暴露。

目标：消除 cgo（Go 方案 B 用 plugin.Open，零 cgo），AgentFlow 细化为清晰的三层——底座提供基础设施、SDK+Runtime 提供通用能力、Agent 层只关注业务逻辑。

## 最终确认的关键决策

| # | 决策点 | 结论 |
|---|--------|------|
| ① | SDK 实现方式 | 三种语言各写一份（C++ SDK / Rust SDK / Go SDK） |
| ② | Runtime 部署 | 3 个独立进程，各自通过 UDS 与底座通信，语言间不混合 |
| ③ | Go .so 加载 | `go build -buildmode=plugin` → Go Runtime 用 `plugin.Open()` 加载，零 cgo |
| ④ | Rust .so 加载 | `cdylib` + `extern "C"` → Rust Runtime 用 `dlopen()` 加载，FFI 是语言内置 |
| ⑤ | C++ .so 加载 | C++ Runtime 用 `dlopen()` 加载，原生零开销 |
| ⑥ | Go ↔ C++/Rust 混进程 | 不允许，方案 A：3 个独立进程 |
| ⑦ | BaseNode ↔ SDK 关系 | BaseNode.execute() 通过 SDK 获取数据和服务 |
| ⑧ | SKILL 执行 | Runtime 调用 SDK.exec_skill()，子进程执行 |
| ⑨ | 底座职责 | 解析 workflow → 调度 Node 执行 → 提供 DataBus，不关心 Node 语言 |

---

## 三层架构图

```
┌──────────────────────────────────────────────────────────────────────────┐
│                                                                            │
│  第1层：Agent（开发者编写，编译为 .so）                                      │
│                                                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │                          配置层（纯 JSON，语言无关）                    │ │
│  │  ┌────────────┐   ┌───────────────┐   ┌──────────────────────┐       │ │
│  │  │ agent.json │   │ workflow.json │   │ skill_config.json    │       │ │
│  │  │ session/   │   │ node 编排 DAG │   │ activation_patterns  │       │ │
│  │  │ task 定义  │   │ inputs/outputs│   │ entry_path (if exec) │       │ │
│  │  └────────────┘   └───────────────┘   └──────────────────────┘       │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
│                                                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │                        代码层（各语言 .so）                            │ │
│  │                                                                       │ │
│  │   ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐        │ │
│  │   │   C++ Agent     │ │   Rust Agent    │ │    Go Agent     │        │ │
│  │   │                 │ │                 │ │                 │        │ │
│  │   │ class NodeA     │ │ struct NodeA{}  │ │ type NodeA      │        │ │
│  │   │   : BaseNode {  │ │ impl AgentNode  │ │   struct{} {    │        │ │
│  │   │   execute() {   │ │   for NodeA {   │ │                 │        │ │
│  │   │     sdk->       │ │     fn execute  │ │ func Execute() {│        │ │
│  │   │       call_llm()│ │     sdk.call_llm│ │   sdk.CallLlm() │        │ │
│  │   │   }             │ │   }            │ │ }               │        │ │
│  │   │ }               │ │ }              │ │ }               │        │ │
│  │   ├─────────────────┤ ├────────────────┤ ├─────────────────┤        │ │
│  │   │ 继承 C++        │ │ 实现 Rust      │ │ 实现 Go         │        │ │
│  │   │ BaseNode 基类   │ │ AgentNode trait│ │ AgentNode iface │        │ │
│  │   │ 调用 C++ SDK    │ │ 调用 Rust SDK  │ │ 调用 Go SDK     │        │ │
│  │   └────────┬────────┘ └───────┬────────┘ └───────┬─────────┘        │ │
│  │            │                  │                   │                   │ │
│  │      编译产物             编译产物            编译产物                │ │
│  │   libnode_a.so        libnode_b.so       libnode_c.so               │ │
│  │   (C++ ABI)           (C ABI, ext"C")    (Go ABI, plugin)           │ │
│  └────────────┬─────────────────┬───────────────────┬───────────────────┘ │
│               │                 │                   │                      │
│          各语言原生加载      各语言原生加载      各语言原生加载             │
│               │                 │                   │                      │
└───────────────┼─────────────────┼───────────────────┼──────────────────────┘
                │                 │                   │
    ╔═══════════╧═════════════════╧═══════════════════╧══════════════════════╗
    ║                                                                        ║
    ║  第2层：SDK + Runtime（AgentFlow 提供，三种语言独立实现）                ║
    ║                                                                        ║
    ║  ┌──────────────────────────────────────────────────────────────────┐  ║
    ║  │  SDK（公共能力库，给第1层 Agent 开发者用，不被编译进 AgentFlow）     │  ║
    ║  │                                                                   │  ║
    ║  │         C++ SDK              Rust SDK             Go SDK           │  ║
    ║  │       ┌──────────┐       ┌──────────┐        ┌──────────┐        │  ║
    ║  │       │call_llm()│       │call_llm()│        │CallLlm() │        │  ║
    ║  │       │load_skill│       │load_skill│        │LoadSkill │        │  ║
    ║  │       │exec_tool │       │exec_tool │        │ExecTool  │        │  ║
    ║  │       │databus_* │       │databus_* │        │DataBus_* │        │  ║
    ║  │       └──────────┘       └──────────┘        └──────────┘        │  ║
    ║  └──────────────────────────────┬───────────────────────────────────┘  ║
    ║                                 │ 调用                                  ║
    ║  ┌──────────────────────────────┴───────────────────────────────────┐  ║
    ║  │  BaseNode（执行流程骨架，各语言一份，调用 SDK 获取能力）             │  ║
    ║  │                                                                   │  ║
    ║  │   C++ BaseNode        Rust AgentNode trait    Go AgentNode iface   │  ║
    ║  │  ┌──────────────┐    ┌──────────────────┐    ┌──────────────────┐ │  ║
    ║  │  │ execute()     │    │ fn execute()     │    │ func Execute()   │ │  ║
    ║  │  │ build_msgs()  │    │ fn build_msgs()  │    │ func BuildMsgs() │ │  ║
    ║  │  │ 编排调用流程  │    │ 编排调用流程     │    │ 编排调用流程     │ │  ║
    ║  │  │    │          │    │    │             │    │    │             │ │  ║
    ║  │  │    └── 调 SDK │    │    └── 调 SDK    │    │    └── 调 SDK    │ │  ║
    ║  │  └──────────────┘    └──────────────────┘    └──────────────────┘ │  ║
    ║  └──────────────────────────────────────────────────────────────────┘  ║
    ║                                                                        ║
    ║  ┌──────────────────────────────────────────────────────────────────┐  ║
    ║  │  Runtime（3 个独立进程，各自加载对应语言的 Node .so）               │  ║
    ║  │                                                                   │  ║
    ║  │  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐          │  ║
    ║  │  │ C++ Runtime  │   │ Rust Runtime │   │  Go Runtime  │          │  ║
    ║  │  │   进程       │   │   进程       │   │   进程       │          │  ║
    ║  │  │              │   │              │   │              │          │  ║
    ║  │  │ dlopen()     │   │ dlopen()     │   │ plugin.Open()│          │  ║
    ║  │  │ C++ ABI      │   │ C ABI        │   │ Go ABI       │          │  ║
    ║  │  │ 零开销       │   │ extern "C"   │   │ 零 cgo       │          │  ║
    ║  │  └──────┬───────┘   └──────┬───────┘   └──────┬───────┘          │  ║
    ║  │         │                  │                   │                   │  ║
    ║  │         │         ⚠️ 三个独立进程，互不混合       │                   │  ║
    ║  │         │         各自通过 UDS 与底座通信         │                   │  ║
    ║  └─────────┼──────────────────┼───────────────────┼───────────────────┘  ║
    ║            │                  │                   │                       ║
    ╚════════════╧══════════════════╧═══════════════════╧═══════════════════════╝
                 │                  │                   │
                 │      UDS IPC     │      UDS IPC      │
                 │  (JSON 协议)     │  (JSON 协议)      │
                 │                  │                   │
┌────────────────┴──────────────────┴───────────────────┴──────────────────────┐
│                                                                              │
│  第3层：AgentFlow 底座（编译为 libagentflow.so，C++ 实现）                     │
│                                                                              │
│  ┌────────────┐ ┌──────────┐ ┌─────────────┐ ┌───────────────────────┐       │
│  │  Workflow  │ │ DataBus  │ │ UDS Server  │ │  Session/Task 管理    │       │
│  │  引擎      │ │          │ │             │ │                       │       │
│  │ 解析 JSON  │ │ KV 存储  │ │ 处理 Runtime│ │ 生命周期              │       │
│  │ DAG 拓扑   │ │ 5个命名  │ │ 的 JSON 请求│ │ 并发控制              │       │
│  │ 调度 Node  │ │ 空间隔离 │ │ 转发结果    │ │ pool 管理             │       │
│  └────────────┘ └──────────┘ └─────────────┘ └───────────────────────┘       │
│                                                                              │
│  底座职责（只这三件事）：                                                      │
│    1. 解析 workflow → 知道要调哪个 Node                                       │
│    2. 调度 Node 执行 → 通过 UDS 通知对应语言的 Runtime 启动 Node               │
│    3. 提供 DataBus → Node 间数据共享                                          │
│                                                                              │
│  底座不关心的：Node 用什么语言、.so 怎么加载、LLM 怎么调、Skill 怎么执行         │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 一次完整调用流程（用户输入 "帮我搜机票"）

```
用户输入
  │
  ▼
┌─ 底座 ─────────────────────────────────────────────────────────┐
│ 1. dispatcher 收到消息，创建 session/dialogue_id                 │
│ 2. 读取 agent.json → 找到 skill_selection task                  │
│ 3. 读取 skill_selection_workflow.json → 解析 DAG                │
│ 4. 调度 skill_selection_node:                                    │
│    → UDS 发 JSON 给 C++ Runtime:                                │
│      {"node":"skill_selection","command":"lib/libselect.so",...}│
└──────────────────────────┬─────────────────────────────────────┘
                           │ UDS
┌──────────────────────────▼─────────────────────────────────────┐
│ C++ Runtime 进程:                                               │
│ 5. dlopen("lib/libselect.so") → dlsym("skill_selection_node_v3")│
│ 6. func(inputs, outputs, client)                                │
│    Node 内部通过 C++ SDK:                                        │
│      sdk.call_llm(messages, tools)  → HTTP 调 LLM               │
│      sdk.databus_put("sys", "next_skills", ["search","train"])  │
│ 7. outputs 返回给底座                                           │
└──────────────────────────┬─────────────────────────────────────┘
                           │ UDS
┌──────────────────────────▼─────────────────────────────────────┐
│ 底座:                                                           │
│ 8. 读到 DataBus sys/next_skills = ["search","train-ticket"]     │
│ 9. 从 agent.json 匹配 skill template:                           │
│    search → search_workflow.json, config_dir=skills/search     │
│ 10. 启动 search_workflow → 调度 search_skill_node:               │
│     → UDS 发 JSON 给 Go Runtime:                                │
│       {"node":"search","command":"lib/libsearch.so",...}        │
└──────────────────────────┬─────────────────────────────────────┘
                           │ UDS
┌──────────────────────────▼─────────────────────────────────────┐
│ Go Runtime 进程:                                                │
│ 11. plugin.Open("lib/libsearch.so")                             │
│ 12. 调用 Node.Execute()                                         │
│     Node 内部通过 Go SDK:                                        │
│       sdk.CallLlm(messages, tools)  → HTTP 调 LLM               │
│       sdk.ExecTool("bash", args)    → popen 执行                │
│       sdk.LoadSkill("search")       → 读 skills/search/         │
│       sdk.ExecSkill("search", args) → 子进程执行 Python skill   │
│ 13. 结果返回给底座                                              │
└──────────────────────────┬─────────────────────────────────────┘
                           │ UDS
┌──────────────────────────▼─────────────────────────────────────┐
│ 底座:                                                           │
│ 14. coordinator 收集结果 → 写 DataBus                           │
│ 15. dispatcher 读 response → 返回用户                           │
└────────────────────────────────────────────────────────────────┘
```

---

## 关键实现步骤

### 步骤 1：底座扩展 — UDS 协议标准化

底座新增统一的 UDS 协议，处理 Runtime 发来的 DataBus 读写请求。
底座通过 UDS 向 Runtime 下发 Node 执行指令：

```json
{
  "type": "execute_node",
  "node_id": "search_node",
  "command": "lib/libsearch_node.so",
  "function_name": "search_node",
  "inputs": { "user_input": "帮我搜机票" },
  "session_id": "sess-001",
  "dialogue_id": "dlg-001"
}
```

### 步骤 2：三种语言 SDK 各写一份

C++ SDK、Rust SDK、Go SDK 各自提供：call_llm、load_skill、exec_tool、databus_*。
SDK 不被编译进 AgentFlow，作为独立库供 Agent 开发者使用。

### 步骤 3：三种语言 Runtime 各一进程

C++ Runtime、Rust Runtime、Go Runtime 各自独立进程，通过 UDS 与底座通信。
各自用原生方式加载对应语言的 Node .so。

### 步骤 4：BaseNode 基类/trait/interface

每种语言提供一份执行流程骨架（模板方法模式），开发者在 BaseNode 基础上实现 execute()。

### 步骤 5：示例与验证

每种语言一个 NodeA 等价示例 + 端到端 workflow 验证。

---

## 关键文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `AgentFlow/src/runtime/worker/uds_protocol.h` | 新建 | 底座 ↔ Runtime 统一 UDS JSON 协议定义 |
| `publish/sdk/cpp/agentflow-sdk/` | 新建 | C++ SDK：call_llm/load_skill/exec_tool/databus |
| `publish/sdk/rust/agentflow-sdk/` | 修改 | Rust SDK 重写，去掉 llm/skills/tools 内部实现 |
| `publish/sdk/go/agentflow-sdk-go/` | 重写 | Go SDK 零 cgo，plugin 模式 |
| `agents/system_agent_v3/nodes/base_node/BaseNodeAgent.h` | 修改 | C++ BaseNode 骨架 |
| `publish/runtime/cpp_runtime/` | 新建 | C++ Runtime 独立进程 |
| `publish/runtime/rust_runtime/` | 新建 | Rust Runtime 独立进程 |
| `publish/runtime/go_runtime/` | 新建 | Go Runtime 独立进程，plugin.Open |
| `examples/cpp_node/` | 新建 | C++ Node 示例 |
| `examples/rust_node/` | 修改 | Rust Node 示例 |
| `examples/go_node/` | 修改 | Go Node 示例（-buildmode=plugin） |

---

## 验证方式

1. 底座 UDS 协议测试：模拟 Runtime 发送 DataBus 请求
2. C++ 集成：C++ Runtime → dlopen C++ Node → 执行
3. Rust 集成：Rust Runtime → dlopen Rust Node → 执行
4. Go 集成：Go Runtime → plugin.Open Go Node → 执行
5. 端到端：三种语言 Node 混合 workflow，完整 Agent 流程
