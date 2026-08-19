"""学习工作流 SSE 流式服务（从 views.py 抽离，P1 #7 拆分）。

职责：
    承载 learning 模块 SSE 生成器与流式辅助函数，使 ``views.py`` 仅保留
    类视图（CRUD + 启动/提交），符合"视图层薄、服务层厚"的分层架构。

模块内容：
    1. ``iter_workflow_events`` — 统一的工作流事件生成器（start/restart/stream 共用）
    2. ``pump_sync_events`` — 同步生成器线程泵（SSE 实时性 + PostgreSQL 持久化的关键）
    3. ``safe_publish_workflow_event`` — SSE 流中同步推送 WS 事件的共享工具

架构说明（工具调用 / 审批 / 实时同步）：
    Learning 模块是**预定义学习评测工作流**（StateGraph：planner → retrieval →
    quiz_generator → grading → feedback），不使用 agent 自主决策调用工具，
    因此不需要工具选择、工具调用生命周期事件、审批中断、实时同步等能力。
    知识库选择是用户启动前配置的参数（knowledge_base_ids），非运行时工具选择。
    对应前端代码无 ToolCallCard 渲染、无工具调用 UI。

SSE 实时性设计（为什么用"线程泵"而非 astream）：
    Django runserver（Channels/daphne，ASGI）下，同步生成器若直接在事件循环内
    迭代，LLM 调用会阻塞事件循环，daphne 无法即时 flush 已产出的事件 chunk。
    ``graph.astream`` 又要求 checkpointer 支持 async —— 而学习工作流使用的
    PostgreSQL ``PostgresSaver`` 是同步版（aget_tuple/aput 均 NotImplementedError）。

    最终方案：checkpointer 保持 ``PostgresSaver``（checkpoint 持久化在 PostgreSQL，
    进程重启/服务热重载后提交恢复执行不受影响），同步 ``graph.stream`` 在**专用
    线程**中执行，每个事件经 ``loop.call_soon_threadsafe(queue.put_nowait, evt)``
    投递，async SSE 生成器 ``await queue.get()`` 即时取出——事件循环始终空闲，
    daphne 实时 flush，步骤条逐节点实时推进。
"""

import asyncio
import threading

from Django_xm.apps.core.config import get_logger
from Django_xm.common.event_schema import EventSource, EventType
from Django_xm.common.realtime_events import publish_event

from .resilience import stream_with_resilience

logger = get_logger(__name__)

_STEP_MESSAGES = {
    "planner": "正在生成学习计划...",
    "retrieval": "正在检索相关资料...",
    "quiz_generator": "正在生成练习题...",
    "waiting_for_answers": "等待您提交答案...",
    "grading": "正在评分...",
    "feedback": "正在生成反馈...",
    "end": "工作流已完成",
}


def iter_workflow_events(study_flow, initial_state, config, user_id, thread_id, publish=None):
    """遍历图执行事件流，统一产出 workflow_step / workflow_state_update 事件。

    start/stream 与 restart/stream 两条 SSE 流共用，消除视图层重复的事件处理逻辑。
    基于 ``stream_mode=["values", "custom"]``：
        - custom 事件（节点内 ``emit_step`` 实时写入，含 ``__metadata__``）→ workflow_step，
          代表"节点开始执行"，用于步骤条逐节点推进 active 状态
        - values 事件（节点完成后完整状态）→ workflow_step（跳过 start）+ workflow_state_update，
          驱动学习计划/题目/分数等数据展示

    Args:
        study_flow: 编译后的 StudyFlow
        initial_state: 初始状态；中断恢复传 None
        config: LangGraph 调用配置
        user_id: 用户 ID（WS 同步发布用）
        thread_id: 事件绑定的线程 ID（WS 同步发布用）
        publish: 可选回调 ``publish(event_type, data)``，同步推送 WebSocket 事件

    Yields:
        dict: 事件对象，type 为 ``workflow_step`` / ``workflow_state_update`` /
        ``interrupted``（waiting_for_answers 中断标记，产出后生成器停止）
    """
    for event in stream_with_resilience(
        study_flow.graph, initial_state, config, stream_mode=["values", "custom"]
    ):
        if not event:
            continue

        # 多 stream_mode 下事件为 (mode, chunk) 元组；单模式（兼容）直接为 chunk
        if isinstance(event, tuple) and len(event) == 2:
            mode, chunk = event
        else:
            mode, chunk = "values", event

        # custom 事件：节点内实时步骤（emit_step 写入）
        if mode == "custom":
            step = chunk.get("step", "unknown")
            message = chunk.get("message", "处理中...")
            if publish:
                publish(EventType.WORKFLOW_STEP, {"step": step, "message": message})
            yield {"type": "workflow_step", "data": {"step": step, "message": message}}
            continue

        step = chunk.get("current_step", "unknown")

        # 初始状态（current_step='start'）不重复发布 step 事件：
        # 流开始已手动发送 start，此处若再发会覆盖节点内实时步骤的 active 状态
        if step != "start":
            message = _STEP_MESSAGES.get(step, f"当前步骤: {step}")
            if publish:
                publish(EventType.WORKFLOW_STEP, {"step": step, "message": message})
            yield {"type": "workflow_step", "data": {"step": step, "message": message}}

        if publish:
            publish(EventType.WORKFLOW_STATE_UPDATE, {"step": step, "state": step})
        yield {
            "type": "workflow_state_update",
            "data": {
                "step": step,
                "state": step,
                "learning_plan": chunk.get("learning_plan"),
                "retrieved_docs": chunk.get("retrieved_docs"),
                "quiz": chunk.get("quiz"),
                "score": chunk.get("score"),
                "feedback": chunk.get("feedback"),
                "score_details": chunk.get("score_details"),
                "current_step": step,
            },
        }

        if step == "waiting_for_answers":
            yield {"type": "interrupted", "data": {}}
            return


def pump_sync_events(iterator, loop, queue):
    """在专用线程中迭代同步事件生成器，事件经 ``call_soon_threadsafe`` 泵入队列。

    背景：Django runserver（Channels/daphne，ASGI）下，同步生成器的迭代若直接放在
    事件循环内执行，工作流内部的 LLM 调用会阻塞事件循环，daphne 无法即时 flush
    已产出的事件 chunk（中间步骤从不显示 active）。将同步生成器放入独立线程后，
    事件循环始终空闲，每产出一个事件即投递，SSE 实时推进。

    配合 PostgreSQL ``PostgresSaver``（同步 checkpointer）：checkpoint 持久化在 PG，
    进程重启/热重载后仍可正确恢复中断，submit 的同步 ``invoke`` 恢复执行不受影响。

    Args:
        iterator: 同步事件生成器（``iter_workflow_events(...)`` 的返回值）
        loop: 运行中的事件循环（async 生成器内 ``asyncio.get_running_loop()``）
        queue: 事件投递队列；迭代正常结束后投递 ``None``（EOF 哨兵），
            异常时投递 ``{"type": "error", "data": {"message": ...}}``

    Returns:
        None（作为 ``threading.Thread`` 的 target 使用）
    """
    try:
        for evt in iterator:
            loop.call_soon_threadsafe(queue.put_nowait, evt)
    except Exception as e:
        logger.exception("[Learning Stream] 工作流线程执行失败")
        loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "data": {"message": str(e)}})
    finally:
        loop.call_soon_threadsafe(queue.put_nowait, None)


async def safe_publish_workflow_event(
    event_type: EventType,
    thread_id: str,
    data: dict,
    user_id=None,
) -> None:
    """安全发布工作流事件到 task 频道，吞掉异常以避免影响 SSE 主流程。

    用于在 SSE 流式输出时同步推送 WebSocket 事件，使其他浏览器也能收到进度。
    使用异步 ``publish_event`` 避免在 ASGI 事件循环中阻塞（原 ``publish_event_sync``
    会在事件循环中执行同步 Redis 调用，导致其他请求被阻塞）。
    """
    try:
        payload = {
            "source": EventSource.LEARNING.value,
            "source_id": thread_id,
            "thread_id": thread_id,
            **data,
        }
        await publish_event(
            event_type,
            payload,
            task_id=thread_id,
            user_id=str(user_id) if user_id is not None else None,
        )
    except Exception as e:
        logger.warning(f"[API] publish_event 失败: {event_type}, {e}")
