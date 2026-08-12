# LangGraph 的 interrupt 机制（简要说明）

## 摘要

**interrupt** 是 LangGraph 为实现**人机协同（human-in-the-loop, HITL）**而提供的核心原语：它允许图（graph）在执行到任意节点时**暂停（pause）**，将图状态保存到持久化层，等待外部（通常是人类）输入后再**恢复（resume）**继续执行。其设计目标被官方描述为"模拟 Python `input()` 函数的体验，但能运行在异步、多线程、多机器的生产环境中"（LangChain 官方博客，2024-12-14）。自 v0.2.x（2024 年 12 月）起成为推荐做法，取代了早期以静态断点为主的方式。

---

## 一、两种中断方式

| 维度 | 静态中断（Static Breakpoints） | 动态中断（Dynamic Interrupts） |
|---|---|---|
| 定义位置 | `compile(interrupt_before=[...], interrupt_after=[...])` | 节点内部直接调用 `interrupt()` 函数 |
| 触发方式 | 无条件，每次到达该节点必停 | 条件性，由节点逻辑决定 |
| 携带负载 | 无 payload | 携带任意 JSON 可序列化 payload，并接受任意值返回 |
| 主要用途 | 调试、逐步执行、统一门控 | 生产 HITL 标准：审批、收集输入、编辑、升级 |
| 恢复方式 | `graph.invoke(None, config)` | `Command(resume=value)` |

官方明确指出：**HITL 工作流应使用动态 `interrupt()`，而非静态断点**；静态断点更适合调试场景。

---

## 二、工作原理

1. **暂停**：节点内调用 `interrupt(value)` 时，LangGraph 抛出一个特殊的可恢复异常（`GraphInterrupt`）挂起执行，运行时捕获后：
   - 通过 **checkpointer** 保存当前图状态（可无限期保存）；
   - 将 `value` 作为 payload 暴露给调用方（`invoke` 下出现在 `result["__interrupt__"]`，事件流下出现在 `stream.interrupts`）；
   - 无限期等待外部输入，不占用计算资源。
2. **恢复**：调用方用**相同的 `thread_id`** 调用 `graph.invoke(Command(resume=...), config)`（或 `stream_events`），`resume` 值被送回节点并成为 `interrupt()` 的返回值。
3. **关键行为**：恢复时运行时从**节点开头重新执行整个节点**（之前的节点不会重跑）——因此 `interrupt()` 之前的代码会再执行一遍，副作用必须幂等。

**使用三要素**：Checkpointer（持久化状态，必选）+ `thread_id`（指明从哪个状态恢复）+ `interrupt()` 调用（payload 必须 JSON 可序列化）。

**易错点**：不要用 try/except 包裹 `interrupt()`（实现依赖抛异常）；不要在节点内重排/条件性跳过 `interrupt()`（恢复时索引会错位）；只传 JSON 可序列化值；避免 `while True + interrupt()` 循环（会导致指数级重执行，应用条件边循环替代）。

---

## 三、典型使用场景

- **人工审批（Approve/Reject）**：API 调用、数据库变更、金融交易、发送邮件等关键操作执行前暂停，人类批准/拒绝后通过 `Command(goto=...)` 路由。
- **审查并编辑状态**：人类审查并修正 LLM 输出/图状态（`update_state` + `as_node=`）。
- **审查工具调用**：在敏感工具执行前暂停，允许批准、编辑参数或取消；也可把 `interrupt()` 直接放进工具函数内复用。
- **多智能体多轮对话 / 任务分配确认**：supervisor 委派任务给专业 agent 前，暂停让人类确认分配。
- **输入校验**：收集用户输入（如年龄），无效则通过条件边重新提示，直到有效。

---

## 四、代码示例（审批工作流）

```python
from typing import Literal, Optional, TypedDict
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

class ApprovalState(TypedDict):
    action_details: str
    status: Optional[Literal["pending", "approved", "rejected"]]

def approval_node(state: ApprovalState) -> Command[Literal["proceed", "cancel"]]:
    decision = interrupt({"question": "Approve this action?", "details": state["action_details"]})
    return Command(goto="proceed" if decision else "cancel")

builder = StateGraph(ApprovalState)
builder.add_node("approval", approval_node)
builder.add_node("proceed", lambda s: {"status": "approved"})
builder.add_node("cancel", lambda s: {"status": "rejected"})
builder.add_edge(START, "approval")
builder.add_edge("proceed", END)
builder.add_edge("cancel", END)

graph = builder.compile(checkpointer=InMemorySaver())  # 必须启用 checkpointer
config = {"configurable": {"thread_id": "approval-123"}}

# 第一次运行：暂停在 interrupt 处
initial = graph.stream_events({"action_details": "Transfer $500", "status": "pending"},
                              config=config, version="v3")
print(initial.interrupts)  # -> (Interrupt(value={'question': ..., 'details': ...}),)

# 恢复：resume=True 批准 → 路由到 proceed
resumed = graph.stream_events(Command(resume=True), config=config, version="v3")
print(resumed.output["status"])  # -> approved
```

---

## 五、结论与建议

- **interrupt 是 LangGraph 生产级 HITL 的标准方案**，通过"暂停—保存状态—外部输入—恢复"协议实现人工介入，与 `Command(resume=...)`、checkpointer、`thread_id` 配套使用。
- **生产环境务必使用持久化 checkpointer**（Postgres/SQLite/Redis 等），`InMemorySaver` 仅适合测试，无法跨进程/重启存活。
- **多用户/多会话场景**需用唯一 `thread_id` 隔离会话；多个并行中断需按 `Interrupt.id` 映射对应的 resume 值。
- 官方推荐用事件流 `graph.stream_events(..., version="v3")` 驱动可能中断的图，以便感知 `stream.interrupts`。

---

## 参考文献

1. LangChain 官方文档：Interrupts — https://docs.langchain.com/oss/python/langgraph/interrupts
2. LangGraph API Reference：interrupt — https://reference.langchain.com/python/langgraph/types/interrupt
3. LangChain 官方博客：Making it easier to build human-in-the-loop agents with interrupt (2024-12-14) — https://www.langchain.com/blog/making-it-easier-to-build-human-in-the-loop-agents-with-interrupt
4. langgraph 源码 `langgraph/types.py`（Interrupt, Added in v0.2.24）— https://github.com/langchain-ai/langgraph/blob/main/libs/langgraph/langgraph/types.py
5. LangChain 官方视频：LangGraph interrupt — https://www.youtube.com/watch?v=6t7YJcEFUIY
6. LangGraph 官方文档：Use time-travel（update_state / as_node）— https://docs.langchain.com/oss/python/langgraph/use-time-travel
7. neurals：LangGraph interrupts（静态 vs 动态对比）— https://neurals.ca/tech/langchain/langgraph/interrupts
8. DEV：Interrupts and Commands in LangGraph — https://dev.to/jamesbmour/interrupts-and-commands-in-langgraph-building-human-in-the-loop-workflows-4ngl
