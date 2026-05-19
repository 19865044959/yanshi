# C++ AgentFlow能力解析

## 1. JSON配置解析

agent.json、package.json、entry_node_workflow.json、coordinator_workflow.json之间

详见4个json文件的关系.txt

## 2. Workflow DAG编排

### 举例子

 以 entry_node_workflow.json 为例：

  DAG 结构：

                          ┌── node_a（并行）
                          ├── node_b（并行）
      entry_node ─────────┼── node_c（并行）
             │            ├── gui_channel（并行）
             │            └── object_handle_channel（并行）

  执行过程：

  1. 底座读 workflow.json，解析出 6 个 Node、5 条 Edge
  2. 拓扑排序：entry_node 入度为 0 → 先执行
  3. 执行 entry_node：

    - dlopen("lib/libentry_node.so") → dlsym("entry_node_v3")
    - 输入：input: "帮我搜机票", session_id: "sess-001", dialogue_id: "dlg-001"
    - 输出：user_input: "帮我搜机票", session_id: "sess-001", dialogue_id: "dlg-001", user_name: "user", input: "帮我搜机票"
  4. entry_node 执行完，根据 edges 把输出分发：

    - entry_to_a：把 entry_node 的 user_input, session_id, dialogue_id, user_name, input 分别传给 node_a 的同名输入
    - entry_to_b：同样的数据传给 node_b
    - entry_to_c、entry_to_gui_channel、entry_to_object_handle：同样
  5. 5 个 Node 都没有相互依赖 → 并行执行：

    - node_a dlopen("lib/libnode_a.so")，输出 result_a: "..."
    - node_b dlopen("lib/libnode_b.so")，输出 result_b: "..."
    - node_c dlopen("lib/libnode_c.so")，输出 result_c: "..."
    - gui_channel 输出 gui_result: "..."
    - object_handle_channel 无输出
  6. 全部执行完，workflow 结束

  一句话：底座解析 edges → 拓扑排序 → entry_node 先跑 → 5 个并行跑 → 结束。每条 edge 只做一件事：把 source 的输出值按字段名匹配到 target 的输入上。

### 代码

## 3. Node执行调度

● 现在用 entry_node_workflow.json 作为具体例子，完整走一遍调度流程。

---
  完整调度流程：以 entry_node_workflow.json 为例

  第一步：解析 DSL → 构建 DAG

  底座读入 JSON，WorkflowParser 逐项解析（workflow_parser.cpp:376-394）：

  parse_workflow_root()
    ├─ parse_nodes()   → 6 个 MMNode 对象
    │   entry_node, object_handle_channel, node_a, node_b, node_c, gui_channel
    │
    ├─ parse_edges()   → 5 个 MMEdge 对象
    │   entry→object, entry→a, entry→b, entry→c, entry→gui
    │
    └─ validate_workflow_structure()
         └─ validate_dag()  → DFS 检测无环 ✓

  第二步：拓扑排序（Kahn 算法）

  workflow.cpp:1267-1328，核心逻辑：

  1. 统计每个节点的入度 (in_degree)：
     entry_node:              0  ← 没有边指向它，是起点
     object_handle_channel:   1  ← entry_node 指向它
     node_a:                  1
     node_b:                  1
     node_c:                  1
     gui_channel:             1

  2. 入度为 0 的节点入队：queue = [entry_node]

  3. 循环弹出：
     弹出 entry_node → 移除所有出边 → 5个下游节点入度都变为 0 → 全部入队
     queue = [object_handle_channel, node_a, node_b, node_c, gui_channel]

  4. 拓扑顺序：
     Level 0: [entry_node]
     Level 1: [object_handle_channel, node_a, node_b, node_c, gui_channel]

  第三步：Runtime 按 Level 调度执行

  在 runtime.cpp:2500-2516，worker 线程不断从任务队列取任务，执行完当前节点后，调用 get_executable_nodes() 找到下一批就绪节点：

  Worker 线程循环:
    while (队列不空) {
      task = queue.pop()
      process_node(task)       // 执行单个节点
        ↓
      mark_node_executed(id)   // 标记完成
        ↓
      get_executable_nodes()   // 找到所有依赖已满足的节点
        ↓
      将可执行节点 push 入队列
      notify_all()             // 唤醒其他 worker
    }

  get_executable_nodes() 的判断逻辑（workflow.cpp:979-1011）：

  for (每个 node) {
      bool all_deps_ready = true;
      for (每个 edge) {
          if (edge.target == node.id) {            // 这是我的一条入边
              auto source = get_node(edge.source); // 上游节点
              if (source.state != SUCCESS) {       // 上游还没跑完？
                  all_deps_ready = false;           // 那我不能跑
                  break;
              }
          }
      }
      if (all_deps_ready && node.state == READY) {
          executable.push_back(node);  // 可以跑了
      }
  }

  第四步：完整时序动画

  时间轴 ──────────────────────────────────────────────────────────▶

  Worker-1                    Worker-2        Worker-3    Worker-4    Worker-5
    │                            │               │           │           │
    │ 初始状态: 只有 entry_node 入度为0                              │
    │                            │               │           │           │
    ├─ pop entry_node            │               │           │           │
    ├─ dlopen entry_node.so      │               │           │           │
    ├─ execute()                 │               │           │           │
    │  outputs: user_input,      │               │           │           │
    │  session_id, dialogue_id.. │               │           │           │
    ├─ mark SUCCESS              │               │           │           │
    │                            │               │           │           │
    ├─ get_executable_nodes():   │               │           │           │
    │  所有5个下游入度→0, 全READY │               │           │           │
    │                            │               │           │           │
    ├─ push 5 nodes 到队列       │               │           │           │
    ├─ notify_all()              │               │           │           │
    │                            │               │           │           │
    │    ═══════════ 5个Worker同时被唤醒 ═══════════                │
    │                            │               │           │           │
    ├─ pop object_handle    ─────┤               │           │           │
    │                            ├─ pop node_a   │           │           │
    │                            │               ├─ pop node_b│          │
    │                            │               │           ├─ pop node_c
    │                            │               │           │           ├─ pop gui
    │  5个节点 ═════════════════ 并行执行 ════════════════════════   │
    │                            │               │           │           │
    │  object_handle 读写 DataBus│  node_a LLM调用 │ node_b  LLM │ node_c   │ gui处理
    │  put: object_action,       │  put: result_a │ put:result_b│put:result_c│put:gui_*
    │  object_response           │  NodeA_action  │ NodeB_action│NodeC_action│
    │                            │               │           │           │
    │  各自 mark SUCCESS          │  ✓             │  ✓         │  ✓         │  ✓
    │                            │               │           │           │
    ├─ get_executable_nodes(): 没有更多节点了，5个都是终点                 │
    │                            │               │           │           │
    │  completed_count=6, total=6 → workflow 完成！                  │
    │                            │               │           │           │
    └─ invoke_complete = true ── 发 data_exchange 消息 ──▶ 触发 coordinator

  关键代码路径

  底座启动
    │
    ├─ package.cpp:426  AgentPackage::load_from_directory()
    │    ├─ 读 package.json → workflow 文件列表
    │    └─ 读 agent.json → task 列表 (dispatcher/skill/coordinator)
    │
    ├─ runtime.cpp:522  execute_workflow()
    │    ├─ 从 agent 拿到 workflow 对象 (已解析好的 DAG)
    │    ├─ 克隆一份 (clone()) → 隔离本次执行的 node 状态
    │    ├─ 找到起始节点 → push 到 node_task_queue_
    │    └─ g_worker_cv.notify_all() → 唤醒 worker 线程
    │
    ├─ runtime.cpp:2505  worker 线程取任务
    │    ├─ pop node_task_queue_
    │    ├─ process_node() → 执行节点
    │    │    ├─ hydrate inputs (从 DataBus / edge / workflow state 注入)
    │    │    ├─ 注入 DataBus client
    │    │    ├─ exe_loader.load_and_execute_so_v3() → dlopen → dlsym → func()
    │    │    └─ 收集 outputs
    │    ├─ mark_node_executed()
    │    ├─ get_executable_nodes() → 找到下一批就绪节点
    │    └─ push 到队列 + notify_all()
    │
    └─ 所有 worker 空闲 + 所有节点完成 → workflow 完成
         → 发 data_exchange 消息 → dispatcher/coordinator 处理

  调度策略总结

  ┌──────────┬──────────────────────────────────────────────────┐
  │   维度   │                       机制                       │
  ├──────────┼──────────────────────────────────────────────────┤
  │ 顺序保证 │ Kahn 拓扑排序，严格按 DAG 依赖关系               │
  ├──────────┼──────────────────────────────────────────────────┤
  │ 并行度   │ 同 Level 内所有入度为 0 的节点由线程池并行执行   │
  ├──────────┼──────────────────────────────────────────────────┤
  │ 数据传递 │ Edge 字段映射 + DataBus K/V 存储，双通道         │
  ├──────────┼──────────────────────────────────────────────────┤
  │ 故障处理 │ 支持 retry/skip/goto/abort，可配置 error_handler │
  ├──────────┼──────────────────────────────────────────────────┤
  │ 条件分支 │ Edge 支持 if/switch-case 条件表达式              │
  ├──────────┼──────────────────────────────────────────────────┤
  │ 并发控制 │ node_task_queue_ + condition_variable 通知机制   │
  ├──────────┼──────────────────────────────────────────────────┤
  │ 隔离     │ 每次执行 clone() 工作流，避免并发执行互相污染    │
  └──────────┴──────────────────────────────────────────────────┘

## 4. DataBus共享存储

databus的本质就是KVcache，通过key来存储value和获取value

### 举例子

  节点间通信有两套并行机制，服务于不同场景：

  机制一：Edge 自动传输（workflow 内部，Runtime 自动完成）

  workflow 中每个 node 声明了 inputs 和 outputs，edges 定义了字段名映射。Runtime 在调度时自动完成数据搬运。

  以 entry_node_workflow.json 的 edge 为例：

  // entry_node → node_a
  { "source": "entry_node", "target": "node_a",
    "source_outputs": ["user_input", "session_id", "dialogue_id", "user_name", "input"],
    "target_inputs":  ["user_input", "session_id", "dialogue_id", "user_name", "input"] }

  底层实现在 runtime.cpp:714-795：

  node 执行前：
    1. 读取上游 node 的 outputs (DataMap)
    2. 按 edge 定义的字段名逐一匹配 → 注入到下游 node 的 inputs
    3. 同时自动注入 session_id、dialogue_id、node_id

  node 执行后：
    1. node 函数填充 outputs DataMap
    2. Runtime 缓存 outputs，供下游 edge 匹配使用

  C++ node 入口函数签名就是这个机制的直接体现：

  // entry_node_workflow.json 中 entry_node 的 function_name: "entry_node"
  extern "C" void entry_node(
      const DataMap& inputs,   // ← Runtime 注入的，从 edge 或 DataBus 来
      DataMap& outputs,        // ← 你填好，Runtime 转发给下游
      INodeDataBusClient* client  // ← DataBus 客户端
  );

---
  机制二：DataBus K/V 存储（跨 workflow 通信，节点主动读写）

  这是 coordinator 获取各节点数据的关键。因为 coordinator 和 node_a/node_b/node_c 不在同一个 workflow 中，它们之间没有 edge，无法通过机制一传数据。

  写入侧（以 node_a 为例），在 common_utils.h:332-370 的模板函数中：

  // execute_work_node<NodeA>("result_a")
  AgentClass agent(inputs, client, ...);
  std::string result = agent.execute();

  outputs[result_key] = result;                          // ← 机制一：给 workflow 内下游
  put_to_databus(client, dialogue_id, result_key, result); // ← 机制二：写入 DataBus

  put_to_databus 的实现（common_utils.h:287-297）：

  bool put_to_databus(client, dialogue_id, key, value) {
      // full_key = "dlg-001/result_a"
      std::string full_key = dialogue_id.empty() ? key : dialogue_id + "/" + key;
      client->user()->put(full_key, value);  // 写入 user 命名空间
  }

  实际在 DataBus 中的完整路径是：

  /session/{session_id}/user/{dialogue_id}/{key}

  例如：/session/sess-001/user/dlg-001/result_a

  同时，BaseNodeAgent 还写了 action/payload（BaseNodeAgent.cpp:260-263）：

  // node_name_ = "NodeA"
  put_to_databus(client, dialogue_id, "NodeA_action", node_action);   // key = "NodeA_action"
  put_to_databus(client, dialogue_id, "NodeA_payload", node_payload); // key = "NodeA_payload"

---
  最终：Coordinator 如何知道数据藏在哪

  答案很直接——硬编码的 key 名称约定。看 Coordinator.cpp:418-427：

  std::string execute_impl() override {
      // ==== 1. 按约定好的 key 名读取 ====
      std::string result_a = get_result_from_databus(databus_client_, dialogue_id_, "result_a");
      std::string result_b = get_result_from_databus(databus_client_, dialogue_id_, "result_b");
      std::string result_c = get_result_from_databus(databus_client_, dialogue_id_, "result_c");

      std::string action_a = get_result_from_databus(databus_client_, dialogue_id_, "NodeA_action");
      std::string action_b = get_result_from_databus(databus_client_, dialogue_id_, "NodeB_action");
      std::string action_c = get_result_from_databus(databus_client_, dialogue_id_, "NodeC_action");
      // ...
  }

  get_result_from_databus 的实现（Coordinator.cpp:19-33）：

  std::string get_result_from_databus(client, dialogue_id, result_key) {
      // key = "dlg-001/result_a"
      std::string key = dialogue_id.empty() ? result_key : dialogue_id + "/" + result_key;
      client->user()->get(key, var);  // 从 user 命名空间读
  }

---
  完整数据流总结

  ┌─ entry_node_workflow.json ────────────────────────────────────┐
  │                                                                 │
  │  entry_node                                                     │
  │    │ outputs: user_input, session_id, dialogue_id, ...          │
  │    │                                                            │
  │    ├──[edge]──→ node_a                                          │
  │    │   inputs ← 自动注入 user_input, session_id, dialogue_id    │
  │    │   执行后:                                                   │
  │    │     outputs["result_a"] = "..."      ← 机制一（给同 workflow 下游）│
  │    │     put_to_databus("result_a", ...)  ← 机制二（写入 DataBus）    │
  │    │     put_to_databus("NodeA_action", ...)                          │
  │    │     put_to_databus("NodeA_payload", ...)                         │
  │    │                                                            │
  │    ├──[edge]──→ node_b    （同上，key = result_b / NodeB_action）│
  │    ├──[edge]──→ node_c    （同上，key = result_c / NodeC_action）│
  │    ├──[edge]──→ object_handle_channel                           │
  │    └──[edge]──→ gui_channel                                     │
  │                                                                 │
  └─────────────────────────────────────────────────────────────────┘
                           │
                           │ 所有节点结果都写入了 DataBus
                           │ Key: {dialogue_id}/result_a, result_b, ...
                           │ Namespace: user
                           │ 完整路径: /session/{sid}/user/{did}/result_a
                           ▼
  ┌─ coordinator_workflow.json ────────────────────────────────────┐
  │                                                                 │
  │  coordinator_node                                               │
  │    inputs ← Runtime 注入 session_id, dialogue_id                 │
  │    执行:                                                         │
  │      get_result_from_databus(client, dialogue_id, "result_a")    │
  │      get_result_from_databus(client, dialogue_id, "result_b")    │
  │      get_result_from_databus(client, dialogue_id, "result_c")    │
  │      get_result_from_databus(client, dialogue_id, "NodeA_action")│
  │      ...                                                        │
  │      → 根据 action 类型决定行为：                                  │
  │        "agent_invoke" → 调用 GUI Agent                           │
  │        "no_reply"    → 直接返回                                  │
  │        "" (空)       → 调用 LLM 汇总所有结果                      │
  │                                                                 │
  └─────────────────────────────────────────────────────────────────┘

## 5. UDS进程间通信

runtime与底座间通信，底座 node信息--> runtime --> 输出 --> 底座coordinator

## 6. Session/Task生命周期管理

## 7. Skill子进程执行

## 8. LLM流式调用中转

## 9. 文件热加载

## 10. SO版本管理

# 完整时间轴

## 重构前

  重构前（当前架构）：单进程，dlopen 直调

  底座进程 (唯一进程, PID=1000)
  ════════════════════════════════════════════════════════════════════

  [1] 用户消息到达
  [2] Dispatcher 读 agent.json → next_skills: ["entry-workflow"]
  [3] 匹配 skill 模板 → workflow_file: "entry_node_workflow.json"
  [4] 解析 JSON → 构建 DAG (6 节点, 5 边)
  [5] 拓扑排序 → Level 0: [entry_node], Level 1: [5个并行]
  [6] clone workflow 隔离本次执行

  [7] Worker 线程执行 entry_node:
      ├─ dlopen("lib/libentry_node.so")      ← 加载到当前进程
      ├─ dlsym(handle, "entry_node_v3")
      ├─ func(inputs, outputs, client)       ← 直接函数调用,同一堆
      │   ├─ 内部调 LLM: HTTP请求直接发出
      │   └─ put_to_databus → client->user()->put()
      │       └─ std::map[key] = value       ← 直接写进程内 map
      └─ 收集 outputs

  [8] get_executable_nodes() → 5 个下游入度为0,全部就绪

  [9] 5 个 Worker 并行执行 (全在同一进程):
      ┌─ Worker-1: dlopen node_a.so → func() → LLM → put result_a
      ├─ Worker-2: dlopen node_b.so → func() → LLM → put result_b
      ├─ Worker-3: dlopen node_c.so → func() → LLM → put result_c
      ├─ Worker-4: dlopen object_handle.so → put object_action
      └─ Worker-5: dlopen gui_channel.so → put gui_action

  [10] 全部完成 → completed_count=6, total=6 → workflow 完成

  [11] 触发 coordinator workflow:
       ├─ dlopen("lib/libcoordinator.so")
       ├─ func(inputs, outputs, client)
       │   ├─ get_result_from_databus("result_a")  ← 直接读进程内 map
       │   ├─ get_result_from_databus("result_b")
       │   ├─ get_result_from_databus("result_c")
       │   ├─ 调 LLM 汇总
       │   └─ send_uds_output() → 发给外部 plugin
       └─ 返回最终回复

  [12] 回复 → 用户

  全程 1 个进程，所有 .so 共享堆内存，DataBus = std::map，无 IPC



## 重构后

  重构后（三层架构）：多进程，UDS IPC

  底座进程(PID=1000)    C++ Runtime(PID=1001)  Go Runtime(PID=1002)  Rust Runtime(PID=1003)
  ══════════════════    ════════════════════    ═══════════════════    ════════════════════

  [1] 用户消息到达
  [2] Dispatcher → agent.json
  [3] 匹配 skill → workflow
  [4] 解析 DAG
  [5] 拓扑排序
  [6] clone workflow

  ━━━━━━━━━━━━━━ 以下是重构后新增/变化的步骤 ━━━━━━━━━━━━━━

  ★ [7] 底座查 node 语言 → 选 Runtime:
      entry_node: C++   → 目标 C++ Runtime
      node_a:     C++   → 目标 C++ Runtime
      node_b:     Rust  → 目标 Rust Runtime
      node_c:     Go    → 目标 Go Runtime

  ★ [8] 底座 → UDS → C++ Runtime:
      {
        "type": "execute_node",
        "node_id": "entry_node",
        "command": "lib/libentry_node.so",
        "function_name": "entry_node_v3",
        "inputs": {"user_input":"帮我搜机票",...},
        "session_id": "sess-001",
        "dialogue_id": "dlg-001"
      }

  ★ [9] C++ Runtime 收到,执行 entry_node:
      ├─ dlopen("lib/libentry_node.so")
      ├─ dlsym → func
  ★   ├─ 注入 SDK 实例 (不是直接传 client 指针)
  ★   │   SDK 内部封装了:
  ★   │     call_llm()    → HTTP 调 LLM
  ★   │     databus_put() → UDS 发给底座   ← 不再是直接 std::map
  ★   │     load_skill()  → 读本地 SKILL 文件
  ★   │     exec_tool()   → popen 子进程
      ├─ func(inputs, outputs, sdk)
      │   ├─ sdk.call_llm(messages, tools)
      │   │   └─ HTTP POST → LLM 服务
      │   └─ sdk.databus_put("user_input", value)
  ★   │       └─ UDS send → 底座:
  ★   │           {"method":"put","params":{session_id,namespace:"user",
  ★   │            key:"dlg-001/user_input",value:"..."}}
      └─ 返回 outputs

  ★ [10] C++ Runtime → UDS → 底座:
      {
        "type": "execute_result",
        "node_id": "entry_node",
        "success": true,
        "outputs": {"user_input":"...","session_id":"...","dialogue_id":"..."}
      }

  [11] 底座接收 → mark_node_executed → get_executable_nodes()
       → 5 个下游就绪

  ★ [12] 底座根据语言分别发送 UDS 命令:
       ┌─ UDS → C++ Runtime:  {"node_id":"node_a","command":"lib/libnode_a.so",...}
       ├─ UDS → Rust Runtime: {"node_id":"node_b","command":"lib/libnode_b.so",...}
       ├─ UDS → Go Runtime:   {"node_id":"node_c","command":"lib/libnode_c.so",...}
       ├─ UDS → C++ Runtime:  {"node_id":"object_handle_channel",...}
       └─ UDS → C++ Runtime:  {"node_id":"gui_channel",...}

  ★ [13] 三个 Runtime 并行执行 (各自独立进程):

​       C++ Runtime (1001)          Rust Runtime (1002)       Go Runtime (1003)
​       ═══════════════════         ═══════════════════       ═══════════════════

  ★    dlopen node_a.so        ★  dlopen node_b.so      ★  plugin.Open node_c.so

  ★    (C++ ABI, 原生)         ★  (C ABI, extern "C")   ★  (Go ABI, 零 cgo)
          │                          │                         │
       func(inputs,out,sdk)      execute(inputs,out,sdk)    Execute(inputs,out,sdk)
          │                          │                         │
       sdk.call_llm()             sdk.call_llm()            sdk.CallLlm()
          │                          │                         │
  ★    sdk.databus_put()     ★  sdk.databus_put()      ★  sdk.DataBusPut()
  ★       │                       │                         │
  ★       └─UDS→底座:put result_a  └─UDS→底座:put result_b   └─UDS→底座:put result_c

  ★  三个 Runtime 各自通过 UDS 向底座读写 DataBus，互不直接通信

  ★ [14] 各 Runtime → UDS → 底座返回执行结果

  [15] 全部完成 → workflow 完成

  ★ [16] 底座触发 coordinator (假设是 Go):
       UDS → Go Runtime: {"node_id":"coordinator","command":"lib/libcoordinator.so",...}

  ★ [17] Go Runtime 执行 coordinator:
       plugin.Open("libcoordinator.so")
       Execute(inputs, outputs, sdk)
         ├─ sdk.DataBusGet("result_a")  ──UDS──▶ 底座查询
         ├─ sdk.DataBusGet("result_b")  ──UDS──▶ 底座查询
         ├─ sdk.DataBusGet("result_c")  ──UDS──▶ 底座查询
         ├─ sdk.CallLlm(messages)       ──HTTP──▶ LLM 服务
         └─ 返回最终回复

  ★ [18] Go Runtime → UDS → 底座 → 返回用户





