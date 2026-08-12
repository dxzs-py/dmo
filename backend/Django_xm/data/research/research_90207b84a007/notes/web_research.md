# LangGraph interrupt 机制 — 网络研究笔记

> 研究日期：基于当前文档体系（官方文档已迁移至 docs.langchain.com，LangGraph v0.2+ / v1.x）
> 核心结论：`interrupt()` 是 LangGraph 人机协同（human-in-the-loop, HITL）工作流的第一等公民原语（first-class primitive），取代了早期以静态断点（breakpoints）为主的方式，为生产环境提供了"暂停—外部输入—恢复"的完整协议。

## 一、各主题搜索发现

### 主题 1：interrupt 核心机制
interrupt 机制允许在图执行的任意点暂停，等待外部输入后再继续。触发时 LangGraph 通过持久化层（persistence layer）保存图状态，无限期等待恢复。动态中断（`interrupt()` 函数）与静态断点（pause before/after 特定节点）相对，可放在代码任意位置且可基于应用逻辑条件触发。

### 主题 2：human-in-the-loop 人机协同
LangGraph 从设计之初就把持久化（persistence）作为第一公民，每步图读写 checkpoint，因此天然支持 HITL。2024-12-14 LangChain 官方博客宣布 interrupt，设计目标是模拟 Python `input()` 函数体验（`response = interrupt("Your question here")`），但可工作在异步、多线程、多机器生产环境。

### 主题 3：interrupt_before / interrupt_after（静态中断）
在 `compile()` 时通过 `interrupt_before=[...]` / `interrupt_after=[...]` 指定，在节点执行前/后无条件暂停。官方明确标注：**静态中断不推荐用于 HITL，应使用 `interrupt()` 函数**；更适合用于调试（逐步执行图）。恢复方式为 `graph.invoke(None, config)`。

### 主题 4：动态 interrupt + Command(resume=...)
`Command(resume=...)` 是唯一设计为 `invoke()`/`stream()`/`stream_events()` 输入的 Command 模式。恢复值成为节点内 `interrupt()` 调用的返回值。并行分支可同时中断，需按 `Interrupt.id` 映射 resume 值。官方推荐用 `graph.stream_events(..., version="v3")` 驱动可能中断的图。

### 主题 5：版本要求
`interrupt` 函数自 v0.2 起可用；`Interrupt` 信息类 "Added in version 0.2.24"。使用前提：**必须启用 checkpointer**；生产环境用持久化 checkpointer（Postgres/SQLite/MongoDB），测试用 `InMemorySaver`/`MemorySaver`。

## 二、静态中断 vs 动态中断

| 维度 | 静态中断（Static Breakpoints） | 动态中断（Dynamic Interrupts） |
|---|---|---|
| 定义位置 | `compile(interrupt_before/after=...)` | 节点内部调用 `interrupt()` |
| 触发方式 | 无条件，每次访问该节点必停 | 条件性，仅当逻辑决定时暂停 |
| 携带负载 | 无 payload | 携带任意 JSON 可序列化 payload，接受任意值返回 |
| 主要用途 | 调试、统一门控 | 生产 HITL 标准：审批、编辑、选择、升级 |
| 恢复方式 | `graph.invoke(None, config)` | `Command(resume=value)` |

两者互补而非竞争。

## 三、工作原理

**暂停过程**：节点内调用 `interrupt(value)` 时，LangGraph 抛出特殊可恢复异常 `GraphInterrupt` 挂起执行，运行时捕获后：
- 用 checkpointer 保存图状态（可无限期保存）；
- 把 `value` 作为 payload 暴露给调用方（invoke 下在 `result["__interrupt__"]`，事件流下在 `stream.interrupts`）；
- 无限期等待外部输入，不占用计算资源。

**恢复过程**：调用方用相同 `thread_id` 调用 `graph.invoke(Command(resume=...), config)`，resume 值送回节点，成为 `interrupt()` 返回值。**关键行为**：恢复时运行时从节点开头重新执行整个节点（之前的节点不重跑）。

**恢复的两种方式**：
- `Command(resume=...)`：直接回答待处理中断（标准方式）。
- `update_state`：编辑状态、创建新 checkpoint，可用 `as_node=` 指定更新归属节点（配合静态断点修正 LLM 输出）。

## 四、使用三要素与易错点

三要素：Checkpointer + thread_id + interrupt() 调用（payload 必须 JSON 可序列化）。

易错点（Rules of Interrupts）：
- 不要用 try/except 包裹 interrupt()（实现依赖抛异常，裸捕获会使中断失效）
- 不要在节点内重排或条件性跳过 interrupt()（恢复时节点从头重跑，索引会错位）
- 只传 JSON 可序列化值
- interrupt() 之前的副作用必须幂等（节点会重跑）
- 避免 while True + interrupt() 循环（指数级重执行），用条件边循环

## 五、典型使用场景
1. 人工审批（Approve/Reject）：关键操作执行前暂停
2. 审查并编辑状态（Review & Edit State）
3. 审查工具调用（Review Tool Calls）：可把 interrupt() 放进工具内
4. 多智能体多轮对话
5. 多智能体任务分配确认（Supervisor 模式）
6. 输入校验（收集用户输入，无效则重新提示）
7. HITL 中间件/Deep Agents 集成

## 六、代码示例

### 示例 1：审批工作流（interrupt + Command 路由）
```python
from typing import Literal, Optional, TypedDict
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

class ApprovalState(TypedDict):
    action_details: str
    status: Optional[Literal["pending", "approved", "rejected"]]

def approval_node(state: ApprovalState) -> Command[Literal["proceed", "cancel"]]:
    decision = interrupt({
        "question": "Approve this action?",
        "details": state["action_details"],
    })
    return Command(goto="proceed" if decision else "cancel")

def proceed_node(state: ApprovalState):
    return {"status": "approved"}

def cancel_node(state: ApprovalState):
    return {"status": "rejected"}

builder = StateGraph(ApprovalState)
builder.add_node("approval", approval_node)
builder.add_node("proceed", proceed_node)
builder.add_node("cancel", cancel_node)
builder.add_edge(START, "approval")
builder.add_edge("proceed", END)
builder.add_edge("cancel", END)

graph = builder.compile(checkpointer=InMemorySaver())  # 必须启用 checkpointer
config = {"configurable": {"thread_id": "approval-123"}}

# 第一次运行：暂停在 interrupt 处
initial = graph.stream_events(
    {"action_details": "Transfer $500", "status": "pending"},
    config=config, version="v3",
)
print(initial.interrupts)  # -> (Interrupt(value={'question': ..., 'details': ...}),)

# 恢复：resume=True 批准（路由到 proceed）
resumed = graph.stream_events(Command(resume=True), config=config, version="v3")
print(resumed.output["status"])  # -> approved
```

### 示例 2：收集用户输入（最小演示）
```python
import uuid
from typing import Optional
from typing_extensions import TypedDict
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.constants import START
from langgraph.graph import StateGraph
from langgraph.types import Command, interrupt

class State(TypedDict):
    foo: str
    human_value: Optional[str]

def node(state: State):
    answer = interrupt("what is your age?")
    print(f"> Received an input from the interrupt: {answer}")
    return {"human_value": answer}

builder = StateGraph(State)
builder.add_node("node", node)
builder.add_edge(START, "node")
graph = builder.compile(checkpointer=InMemorySaver())

config = {"configurable": {"thread_id": uuid.uuid4()}}
for chunk in graph.stream({"foo": "abc"}, config):
    print(chunk)  # > {'__interrupt__': (Interrupt(value='what is your age?', ...),)}

command = Command(resume="some input from a human!!!")
for chunk in graph.stream(command, config):
    print(chunk)
```

### 示例 3：工具调用审查（把 interrupt 放进工具内）
```python
from langchain.tools import tool
from langgraph.types import interrupt

@tool
def send_email(to: str, subject: str, body: str):
    """Send an email to a recipient."""
    response = interrupt({
        "action": "send_email", "to": to, "subject": subject, "body": body,
        "message": "Approve sending this email?",
    })
    if response.get("action") == "approve":
        final_to = response.get("to", to)
        final_subject = response.get("subject", subject)
        return f"Email sent to {final_to} with subject '{final_subject}'"
    return "Email cancelled by user"
```

## 七、信息来源 URL

**官方文档（主来源）**
- Interrupts（Python 完整文档）— https://docs.langchain.com/oss/python/langgraph/interrupts
- interrupt | langgraph API Reference — https://reference.langchain.com/python/langgraph/types/interrupt
- langgraph 源码 types.py — https://github.com/langchain-ai/langgraph/blob/main/libs/langgraph/langgraph/types.py
- Deep Agents Human-in-the-loop — https://docs.langchain.com/oss/python/deepagents/human-in-the-loop
- Use time-travel（update_state / as_node / fork）— https://docs.langchain.com/oss/python/langgraph/use-time-travel

**官方博客与视频**
- Making it easier to build human-in-the-loop agents with interrupt（2024-12-14）— https://www.langchain.com/blog/making-it-easier-to-build-human-in-the-loop-agents-with-interrupt
- LangGraph interrupt 官方视频 — https://www.youtube.com/watch?v=6t7YJcEFUIY

**社区与技术资料**
- LangGraph interrupts · neurals — https://neurals.ca/tech/langchain/langgraph/interrupts
- LangGraph HITL Design Patterns — https://medium.com/fundamentals-of-artificial-intelligence/langgraph-hitl-design-patterns-debug-with-interrupts-93f4e5df7641
- LangGraph HITL: Pausing, Reviewing, and Rewinding — https://pub.towardsai.net/langgraph-human-in-the-loop-pausing-reviewing-and-rewinding-your-agent-4028bd05b049
- Building Intelligent HITL Workflows with LangGraph Interrupts — https://medium.com/@riddhimansherlekar/building-intelligent-human-in-the-loop-workflows-with-langgraph-interrupts-and-rag-in-a-multi-agent-4ed7b51fb6ff
- DEV: Interrupts and Commands in LangGraph — https://dev.to/jamesbmour/interrupts-and-commands-in-langgraph-building-human-in-the-loop-workflows-4ngl
- LangChain 论坛：Interrupts 机制与 FastAPI 集成 — https://forum.langchain.com/t/request-for-guidance-when-using-the-interrupts-mechanism-in-langgraph-how-should-the-graph-utilize-ainvoke-when-multiple-interrupts-exist-how-to-integrate-the-interrupts-mechanism-with-other-frameworks-e-g-the-fastapi-framework/2821

## 八、补充说明
1. 文档迁移：原 `langchain-ai.github.io/langgraph/` 已重定向至 `docs.langchain.com`。
2. 版本：动态 interrupt 机制在 v0.2.x 中期（2024-12）随官方博客发布成熟；v1.x 中 `stream_events(version="v3")` 为推荐驱动方式。
3. 生产建议：MemorySaver 进程内存储无法跨部署/重启存活；长时间等待审批的生产场景必须使用持久化 checkpointer，配合 thread_id 管理多用户会话。
