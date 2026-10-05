"""审批恢复协议边界模块（E6 三边界拆分：resume_protocol）。

职责（audit B 组 §2.3-3；其中"重试指令消息构造"已随 E4 死代码删除不复存在）：
- ``handle_resume_interrupt``：单个 ``__interrupt__`` 条目的协议处理——
  审批中断解析 → 实时通知审批回调（暂停/恢复超时计时）→ 按
  ``langgraph_resume_id`` 分组写入 ``resume_values``；业务等待挂起判定
  （wait_for_subagent → 返回挂起 info，由调用方终止流）。

纯协议函数（无会话状态；resume_values 由调用方持有并原地写入）。
被 ``adapter.py`` 的 ``_handle_updates`` 消费（公共执行循环 updates 模式）。

与 SessionExecutor 的边界（audit B 组 §2.2）：adapter/graph 内侧管 resume
语义（本模块），executor 外侧管 DB 审批批次（B4/B5 恢复链），边界靠
``on_interrupt 回调返回 decisions dict`` 这一条细线维持——本模块不触碰 DB。
"""

import asyncio
from typing import Any

from Django_xm.apps.approvals.services.approval_batch import assert_non_empty_decision
from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)


async def handle_resume_interrupt(
    intr: Any,
    *,
    on_interrupt,
    timeout_mgr,
    resume_values: dict,
) -> dict[str, Any] | None:
    """处理单个 ``__interrupt__`` 条目（审批决策收集 / 业务等待挂起判定）。

    原样迁移自 ``astream_research_with_interrupts`` 的 ``_handle_updates``
    内联逻辑（E6 三边界拆分：resume_protocol），行为等价：

    - 业务等待挂起（spec D4，wait_for_subagent 触发）：返回挂起 info dict，
      调用方写入 ``suspend_box["suspend"]`` 后退出流（不产出审批 UI、
      不 Command(resume)，由外层返回 suspended 结果）；
    - 审批中断：公共解析器统一批量/单工具格式 → 实时通知审批回调
      （会话级单执行流：空 dict 决策 = 审批创建失败，抛错终止）→
      按 langgraph_resume_id 分组写入 resume_values；
    - 解析为空 / 非审批非等待 interrupt：跳过（返回 None）。

    Args:
        intr: LangGraph ``__interrupt__`` 列表中的单条目
            （Interrupt 实例 / dict / 其他）
        on_interrupt: 审批中断回调（签名 on_interrupt(interrupts) -> decisions；
            None 时直接以 False 决策填充 resume_values）
        timeout_mgr: ExecutionTimeoutManager（回调期间暂停超时计时）
        resume_values: 决策累积 dict（原地写入；Command(resume=...) 的 key
            必须是 LangGraph Interrupt.id）

    Returns:
        业务等待挂起 info dict（{"subagent_thread_ids", "interrupt_id"}）；
        None = 非挂起（决策已写入 resume_values 或条目被跳过）。
    """
    from langgraph.types import Interrupt

    from Django_xm.apps.tools.base import is_approval_interrupt, is_subagent_wait_interrupt

    if isinstance(intr, Interrupt):
        interrupt_value = intr.value
        interrupt_id = intr.id
    elif isinstance(intr, dict):
        interrupt_value = intr.get("value", intr)
        interrupt_id = intr.get("id", "")
    else:
        interrupt_value = intr
        interrupt_id = ""

    # 业务等待挂起（spec D4）：wait_for_subagent 触发，
    # 非审批 interrupt，不产出审批 UI。记录挂起信息后
    # 退出流，由外层返回 suspended 结果（父协程退出）。
    if is_subagent_wait_interrupt(interrupt_value):
        _wait_ids = interrupt_value.get("subagent_thread_ids", []) or []
        logger.info(
            f"[OfficialDeepAgent] 业务等待挂起: "
            f"subagents={_wait_ids}, "
            f"interrupt_id={interrupt_id}"
        )
        return {
            "subagent_thread_ids": _wait_ids,
            "interrupt_id": interrupt_id,
        }

    if not is_approval_interrupt(interrupt_value):
        return None

    # 使用公共审批中断解析器，统一批量/单工具两种格式
    from Django_xm.common.approval_parser import parse_approval_interrupt as _parse_interrupt

    interrupt_list = _parse_interrupt(
        interrupt_value,
        graph_interrupt_id=interrupt_id,
        langgraph_resume_id=interrupt_id,
    )
    if not interrupt_list:
        return None

    is_batch = len(interrupt_list) > 1
    logger.info(
        f"[OfficialDeepAgent] 审批中断: "
        f"{'批量' if is_batch else '单工具'}, "
        f"{len(interrupt_list)} 个工具, "
        f"tools={[b['tool_name'] for b in interrupt_list]}"
    )
    if on_interrupt is not None:
        logger.info(
            f"[OfficialDeepAgent] 实时通知审批回调"
            f"{'(批量)' if is_batch else ''}: "
            f"{len(interrupt_list)} 个工具"
        )

        async def _call_interrupt_handler(interrupts):
            """调用审批中断回调（暂停超时计时）。

            会话级单执行流：on_interrupt 必须返回非空决策；
            空 dict 表示审批创建失败，直接抛错终止（不再走
            Celery 时代"退出等待外部恢复"的 Path D 语义）。
            """
            timeout_mgr.pause()
            try:
                result = on_interrupt(interrupts)
                if asyncio.iscoroutine(result):
                    result = await result
            finally:
                timeout_mgr.resume()
            assert_non_empty_decision(result)
            return result

        batch_resume = await _call_interrupt_handler(interrupt_list)
        # 按 langgraph_resume_id 分组构造 resume dict
        # Command(resume=...) 的 key 必须是 LangGraph Interrupt.id
        langgraph_id = interrupt_list[0].get("langgraph_resume_id", "")
        if isinstance(batch_resume, dict):
            if langgraph_id:
                resume_values.setdefault(
                    langgraph_id, {}
                ).update(batch_resume)
            else:
                resume_values.update(batch_resume)
        elif langgraph_id:
            for bi in interrupt_list:
                resume_values.setdefault(
                    langgraph_id, {}
                )[bi["interrupt_id"]] = batch_resume
        else:
            for bi in interrupt_list:
                resume_values[
                    bi["interrupt_id"]
                ] = batch_resume
    else:
        langgraph_id = interrupt_list[0].get("langgraph_resume_id", "")
        if langgraph_id:
            for bi in interrupt_list:
                resume_values.setdefault(
                    langgraph_id, {}
                )[bi["interrupt_id"]] = False
        else:
            for bi in interrupt_list:
                resume_values[bi["interrupt_id"]] = False

    return None
