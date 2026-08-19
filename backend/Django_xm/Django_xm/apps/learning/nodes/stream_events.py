"""节点内实时步骤事件（SSE/WS 双通道步骤条驱动）。

LangGraph 的 ``stream_mode="values"`` 只在**节点完成后**输出状态，
导致步骤条无法在节点执行过程中（如 LLM 调用耗时期间）推进 active 状态。
本模块通过 ``get_stream_writer()`` 在节点**开始执行时**写入步骤事件，
配合 ``stream_mode="custom"`` 实时发布 ``workflow_step``，实现前端
步骤条逐节点推进。

graph.invoke / 非 custom 流式模式下 ``get_stream_writer()`` 返回 no-op，
调用安全，不改变既有非流式执行行为。
"""

from langgraph.config import get_stream_writer

from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)


def emit_step(step: str, message: str) -> None:
    """发布实时步骤事件（非流式上下文下为 no-op，安全）。"""
    try:
        get_stream_writer()({"step": step, "message": message})
    except Exception as e:
        logger.warning(f"[Stream Events] 写入步骤事件失败: step={step}, {e}")
