"""公共执行循环骨架（spec D8）。

统一 chat 与 research 底层 langgraph ``CompiledStateGraph`` 的流式执行循环骨架：

    astream 双流模式 → updates 检测 interrupt → messages 处理 chunk → Command(resume) 重入

设计边界：

- 本模块是纯骨架，不依赖 apps 层：执行内核差异（chunk 处理 / 审批创建 /
  子代理事件）与中断判定（审批 / 业务等待）均通过回调注入，避免循环依赖。
- 韧性能力在骨架内统一承载：
  - 软超时检查（``check_soft_timeout``）
  - 重复工具调用警告注入（``control.warnings``）
  - 子代理重试指令注入（``control.retry_messages``）
  以上三者的"检测 → 中断流 → 注入 graph state → 以 ``current_input=None``
  重入 astream（从 checkpoint 续流）"循环由本骨架实现；检测本身由 chunk
  处理器（回调）完成并通过 ``ChunkLoopControl`` 回传。
- 审批挂起（协程内 await 决策）与业务等待挂起（``suspend_box``，协程退出 +
  调度器唤醒）在骨架内显式分支：
  - 审批中断 → ``handle_updates`` 回调内 await 决策并写入 ``resume_values``，
    由调用方以 ``Command(resume=resume_values)`` 重入（骨架不构造 Command）。
  - 业务等待挂起 → ``handle_updates`` 写入 ``suspend_box["suspend"]``，
    骨架退出 astream 且不注入/不重入，由调用方返回 suspended 结果。

超时计时暂停（``timeout_mgr.pause/resume``）由审批决策 await 处（回调内）执行，
骨架将 ``timeout_mgr`` 透传给回调（chat/research 的审批处理在 await 决策前后
显式 pause/resume，避免用户审批思考时间计入执行时长）。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# astream 双流模式：messages（流式消息）+ updates（interrupt 事件）
STREAM_MODE = ("messages", "updates")


@dataclass
class ChunkLoopControl:
    """chunk 处理器向主循环回传的控制信号。

    - warnings：待注入 graph state 的重复工具调用警告消息（SystemMessage 列表）
    - retry_messages：待注入 graph state 的子代理重试指令消息（SystemMessage 列表）
    - should_break：updates 处理器请求立即中断当前 astream（审批中断已处理，
      交由调用方以 Command(resume) 重入）
    """

    warnings: list[Any] = field(default_factory=list)
    retry_messages: list[Any] = field(default_factory=list)
    should_break: bool = False


async def run_astream_loop(
    *,
    graph,
    graph_input,
    config,
    process_chunk: Callable,
    handle_updates: Callable,
    resume_values: dict,
    suspend_box: dict,
    timeout_mgr=None,
    check_soft_timeout: bool = False,
    inject_state_messages: Callable[[list], Awaitable[None]] | None = None,
    stream_mode: tuple[str, ...] = STREAM_MODE,
) -> AsyncGenerator[dict, None]:
    """运行单次 astream 执行 pass（含警告/重试注入重入，不含审批 Command(resume) 重入）。

    审批的 ``Command(resume=...)`` 重入由调用方负责（chat 的 ``run_stream_loop``
    与 research 的 ``astream_research_with_interrupts`` 外层循环各自持有其 resume
    语义）；本骨架仅负责单次 astream 内的中断检测、chunk 处理与韧性注入重入。

    Args:
        graph: ``CompiledStateGraph``（含 ``astream`` / 可选 ``aupdate_state``）。
        graph_input: graph 输入（初始 ``{"messages": [...]}`` 或 ``Command(resume=...)``）。
        config: graph 运行配置（含 callbacks / recursion_limit 等）。
        process_chunk: messages 模式 chunk 处理器（async generator），
            签名 ``fn(mode_data, control, resume_values) -> AsyncGenerator[dict, None]``；
            yield 的事件上抛给调用方；通过 ``control`` 回传警告/重试信号。
        handle_updates: updates 模式处理器（async generator），
            签名 ``fn(mode_data, resume_values, suspend_box, control) -> AsyncGenerator[dict, None]``；
            负责审批中断创建/决策收集（写入 ``resume_values``）与业务等待挂起
            （写入 ``suspend_box["suspend"]``）。
        resume_values: 审批恢复决策累积 dict（由调用方清理，审批处理器写入）。
        suspend_box: 业务等待挂起信息回传 dict（写入 ``suspend_box["suspend"]``）。
        timeout_mgr: 执行超时管理器（None 表示不启用）。
        check_soft_timeout: 是否在每轮 chunk 检查 soft timeout（research 启用）。
        inject_state_messages: 注入消息到 graph state 的异步回调，
            如 ``lambda messages: graph.aupdate_state(config, {"messages": messages})``。
        stream_mode: astream 的 stream_mode（默认双流模式）。

    Yields:
        由 ``process_chunk`` / ``handle_updates`` 上抛的事件。
    """
    current_input = graph_input
    control = ChunkLoopControl()
    while True:  # 重复调用警告 / 子代理重试指令注入重入
        control.warnings.clear()
        control.retry_messages.clear()
        control.should_break = False

        async for chunk in graph.astream(
            current_input,
            config=config,
            stream_mode=list(stream_mode),
        ):
            """
            # 对"单个 chunk"来说：是互斥的，不可能同时存在。
            chunk1 → ("messages", AIMessageChunk)  → 走 messages 分支
            chunk2 → ("updates",  {节点:...})      → 走 updates 分支
            chunk3 → ("messages", AIMessageChunk)  → 走 messages 分支
            """
            if check_soft_timeout and timeout_mgr is not None and timeout_mgr.check_soft_timeout():
                logger.warning(f"[ExecutionLoop] 执行超时 (soft): {timeout_mgr.elapsed:.1f}s")

            # 多 stream mode 下 chunk 是 (mode_name, data) 元组
            if isinstance(chunk, tuple) and len(chunk) == 2:
                mode_name, mode_data = chunk
            else:
                mode_name, mode_data = "messages", chunk

            # updates 模式：审批中断 / 业务等待挂起检测
            if mode_name == "updates":
                async for event in handle_updates(mode_data, resume_values, suspend_box, control):
                    yield event
                if suspend_box.get("suspend") or control.should_break:
                    break
                continue

            # messages 模式：chunk 处理 + 韧性检测
            if mode_name == "messages":
                async for event in process_chunk(mode_data, control, resume_values):
                    yield event
                if control.warnings or control.retry_messages:
                    break
                continue

        # astream 结束后统一处理注入 / 重入
        # 业务等待挂起：不注入、不重入，交由调用方返回 suspended 结果
        if suspend_box.get("suspend"):
            break

        # 审批中断已处理：交由调用方 Command(resume) 重入
        if control.should_break:
            break

        # 重复调用警告 / 子代理重试指令：注入 graph state 后以 None 续流
        if control.warnings or control.retry_messages:
            messages_to_inject = list(control.warnings) + list(control.retry_messages)
            if inject_state_messages is not None:
                logger.info(f"[ExecutionLoop] 注入 {len(messages_to_inject)} 条韧性消息到 agent 状态")
                try:
                    await inject_state_messages(messages_to_inject)
                except Exception as e:
                    logger.warning(f"[ExecutionLoop] 注入韧性消息失败: {e}")
            current_input = None  # 从当前 checkpoint 续流
            continue

        break  # astream 正常结束
