"""``wait_for_subagent`` 工具：父 Agent 业务等待挂起（非审批 interrupt，支持批量）。

事件驱动父唤醒（spec D4）的触发点：
- 父 Agent 调用 ``spawn_sub_agent`` 派发一个或多个子代理后拿到 ``subagent_thread_id``；
- 当需要子代理结果推进业务（如撰写报告）时调用本工具，传入全部待等待的 thread_id；
- 本工具触发业务等待 interrupt（``_subagent_wait``，payload 携带 deadline 时间戳），
  父 Graph 持久化 checkpoint 并退出协程（非审批、不产出审批 UI）；
- 正常路径：全部子代理终态后由调度器以 ``Command(resume={subagent_thread_ids: [...]})``
  唤醒父 Graph，本工具返回全部子代理最终结果（经汇总预算压缩）；
- 超时路径：挂起超过 deadline（settings.AGENT_SUBAGENT_WAIT_TIMEOUT）后由
  Celery beat 清扫任务（``subagent_wait_tasks.cleanup_expired_subagent_waits``）
  以超时 resume payload（``_subagent_wait_timeout`` 标记）唤醒，本工具识别标记后
  走失败路径——逐 id 查询 SubAgentInstance 报告真实状态，不伪造结果。

超时唤醒边界语义：超时唤醒不终止子代理执行——父图收到超时结果后继续推进业务，
仍在运行的子代理完成后正常落库（其终态回调找不到父 awaiter 仅记日志，
状态记录不受影响）。返回给父 Agent 的状态为查询时刻的 DB 快照。

汇总预算（fan-in 进入父上下文前收敛，settings 可覆盖）：
- 每条 completed 结果先经 ``context_summary.extract_json_summary`` 结构化压缩
  （自带记录数标注），再硬截断到 AGENT_SUBAGENT_RESULT_MAX_CHARS；
- failed 的错误信息本身短，不参与单条截断；
- 总量超 AGENT_SUBAGENT_RESULTS_TOTAL_BUDGET 时按优先级收敛：
  failed 优先保留，completed 均分剩余预算逐条压缩，无效碎片替换为占位符，
  并在汇总尾部标注"已截断，完整结果见子代理详情"。

与 ``spawn_sub_agent`` 分离：spawn 负责派发（立即返回），wait 负责等待结果。
"""

from __future__ import annotations

import json
import logging
import time

from django.conf import settings
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from Django_xm.apps.context_manager.services.context_summary import extract_json_summary
from Django_xm.apps.tools.errors import TOOL_VERSION

logger = logging.getLogger(__name__)

# 业务等待工具名称（与 spawn_sub_agent 分离，父 Agent 用其等待子代理结果）。
WAIT_FOR_SUBAGENT_TOOL_NAME = "wait_for_subagent"

# 超时 resume payload 标记（subagent_wait_tasks 清扫任务注入的协议路由标识符，
# 非 resume 结果数据；wait_for_subagent 据此走失败路径）。
SUBAGENT_WAIT_TIMEOUT_FLAG = "_subagent_wait_timeout"

# 兜底默认值（与 settings/base.py 显式装配同值，双层兜底，同 concurrency.py 模式）。
# DEFAULT_WAIT_TIMEOUT_SECONDS 对齐 SessionExecutor 挂起 watchdog 7200s；
# DEFAULT_RESULT_MAX_CHARS 对齐 result_preview 提取上限（langgraph_adapter 截 5000）；
# DEFAULT_TOTAL_BUDGET_CHARS 换算依据见 settings/base.py 注释（128k ctx × 25%）。
DEFAULT_WAIT_TIMEOUT_SECONDS = 7200
DEFAULT_RESULT_MAX_CHARS = 5000
DEFAULT_TOTAL_BUDGET_CHARS = 48000

# 总预算收敛时单条 completed 结果的最小保留字符数：低于该值的均分预算不再
# 产生可读摘要，直接替换为占位符，避免"每条留十几个字符"的无效碎片。
MIN_SUMMARY_KEEP_CHARS = 200

TRUNCATED_PLACEHOLDER = "（已截断，完整结果见子代理详情）"
TOTAL_BUDGET_NOTICE = "（汇总超出上下文预算，部分子代理结果已截断，完整结果见子代理详情）"


def _get_positive_int_setting(name: str, default: int) -> int:
    """读取正整数 settings（缺失/非法回退代码默认，防呆与 concurrency.py 同源风格）。"""
    raw_value = getattr(settings, name, default)
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        logger.warning(f"[wait_for_subagent] settings.{name}={raw_value!r} 非法（非数值），回退默认值 {default}")
        return default
    if value < 1:
        logger.warning(f"[wait_for_subagent] settings.{name}={value} 非法（<1），回退默认值 {default}")
        return default
    return value


def get_wait_timeout_seconds() -> int:
    """读取 wait_for_subagent 显式超时（settings.AGENT_SUBAGENT_WAIT_TIMEOUT）。"""
    return _get_positive_int_setting("AGENT_SUBAGENT_WAIT_TIMEOUT", DEFAULT_WAIT_TIMEOUT_SECONDS)


def get_result_max_chars() -> int:
    """读取单条子代理结果上限（settings.AGENT_SUBAGENT_RESULT_MAX_CHARS）。"""
    return _get_positive_int_setting("AGENT_SUBAGENT_RESULT_MAX_CHARS", DEFAULT_RESULT_MAX_CHARS)


def get_total_budget_chars() -> int:
    """读取汇总总预算（settings.AGENT_SUBAGENT_RESULTS_TOTAL_BUDGET）。"""
    return _get_positive_int_setting("AGENT_SUBAGENT_RESULTS_TOTAL_BUDGET", DEFAULT_TOTAL_BUDGET_CHARS)


def _summarize_result_text(text: str, max_chars: int) -> str:
    """单条结果结构化压缩：JSON → 结构化摘要（自带记录数标注），非 JSON 原样；超限硬截断。"""
    if not text:
        return ""
    summarized = extract_json_summary(text)
    if len(summarized) > max_chars:
        return summarized[:max_chars] + "..."
    return summarized


def _render_result_item(item: dict) -> str:
    """渲染单条子代理结果行（status 标记 + 正文）。"""
    sid = item["subagent_thread_id"]
    status = item["status"]
    body = item["body"]
    if status == "completed":
        return f"子代理 {sid} 已完成。结果：\n{body}"
    if status == "failed":
        return f"子代理 {sid} 执行失败：\n{body}"
    return f"子代理 {sid} 未完成（状态：{status}）：\n{body}"


def _normalize_result_items(subagent_results: list[dict], result_max_chars: int) -> list[dict]:
    """规整批量子代理结果：completed 摘要压缩，failed/其他状态正文原样。"""
    items = []
    for raw in subagent_results:
        status = str(raw.get("status", "") or "failed")
        result = raw.get("result", "") or ""
        if status == "completed":
            body = _summarize_result_text(result, result_max_chars) or "（空）"
        elif status == "failed":
            body = result or "未知错误"
        else:
            # running / interrupted 等非终态（超时/非法 resume 查 DB 快照路径）：
            # 正文如实返回，不做有损压缩（状态本身是父 Agent 决策所需信息）。
            body = result or "（无结果）"
        items.append(
            {
                "subagent_thread_id": str(raw.get("subagent_thread_id", "") or ""),
                "status": status,
                "body": body,
                "is_failed": status == "failed",
            }
        )
    return items


def _apply_total_budget(items: list[dict], rendered: list[str], total_budget: int) -> str:
    """总量超预算时按优先级收敛：failed 优先保留，completed 均分剩余预算。

    Returns:
        收敛后的汇总文本（尾部附截断标注）。
    """
    failed_len = sum(len(r) for item, r in zip(items, rendered, strict=True) if item["is_failed"])
    completed_indexes = [i for i, item in enumerate(items) if not item["is_failed"]]
    remaining = max(total_budget - failed_len, 0)
    per_budget = remaining // len(completed_indexes) if completed_indexes else 0
    for i in completed_indexes:
        if per_budget < MIN_SUMMARY_KEEP_CHARS:
            items[i]["body"] = TRUNCATED_PLACEHOLDER
        elif len(items[i]["body"]) > per_budget:
            items[i]["body"] = items[i]["body"][:per_budget] + "..."
        rendered[i] = _render_result_item(items[i])

    # 极端兜底：completed 全部压缩后仍超（failed 群体巨大）→ 全体按均分预算压缩
    rendered = [_render_result_item(item) for item in items]
    if sum(len(r) for r in rendered) > total_budget:
        per_failed = max(total_budget // max(len(items), 1), MIN_SUMMARY_KEEP_CHARS)
        for item in items:
            if len(item["body"]) > per_failed:
                item["body"] = item["body"][:per_failed] + "..."
        rendered = [_render_result_item(item) for item in items]

    return "\n\n".join(rendered) + "\n\n" + TOTAL_BUDGET_NOTICE


def format_subagent_results_text(
    subagent_results: list[dict],
    *,
    result_max_chars: int | None = None,
    total_budget_chars: int | None = None,
) -> str:
    """将批量子代理结果渲染为父 Agent 可读文本（单条压缩 + 总量预算收敛）。

    Args:
        subagent_results: resume 值/DB 查询得到的批量子代理状态
            （[{"subagent_thread_id", "status", "result"}, ...]）。
        result_max_chars: 单条 completed 结果上限（None 读 settings）。
        total_budget_chars: 汇总总预算（None 读 settings）。

    Returns:
        汇总文本；空列表返回"子代理结果缺失"（保持旧语义）。
    """
    if not subagent_results:
        return "子代理结果缺失"

    result_max = result_max_chars if result_max_chars is not None else get_result_max_chars()
    total_budget = total_budget_chars if total_budget_chars is not None else get_total_budget_chars()

    items = _normalize_result_items(subagent_results, result_max)
    rendered = [_render_result_item(item) for item in items]
    if sum(len(r) for r in rendered) <= total_budget:
        return "\n\n".join(rendered)
    return _apply_total_budget(items, rendered, total_budget)


class WaitForSubAgentInput(BaseModel):
    subagent_thread_ids: list[str] = Field(
        description="由 spawn_sub_agent 返回的子代理 thread_id 列表，本工具等待全部完成并返回结果"
    )


class WaitForSubAgentTool(BaseTool):
    name: str = WAIT_FOR_SUBAGENT_TOOL_NAME
    version: str = TOOL_VERSION
    metadata: dict = Field(
        default_factory=lambda: {"tier": "extended", "visibility": "selectable", "category": "agent"}
    )
    description: str = (
        "等待指定的一个或多个子代理全部完成并返回最终结果。调用后当前代理会挂起（保存进度），"
        "待所有子代理执行完毕（成功或失败）后自动恢复并拿到全部结果。"
        "使用时机：已通过 spawn_sub_agent 派发子代理、且后续步骤需要其产出时调用。"
        "参数：subagent_thread_ids-由 spawn_sub_agent 返回的子代理 thread_id 列表（必填，至少 1 个）。"
    )
    args_schema: type[BaseModel] = WaitForSubAgentInput

    def _run(self, subagent_thread_ids: list[str]) -> str:
        return "wait_for_subagent 需要异步执行，请通过 ainvoke 调用"

    async def _arun(self, subagent_thread_ids: list[str]) -> str:
        """触发业务等待挂起（interrupt），并在全部子代理终态后返回结果。"""
        from langgraph.types import interrupt

        from Django_xm.apps.ai_engine.models import SubAgentStatus
        from Django_xm.apps.ai_engine.subagent_runtime import get_subagent_runtime

        if not subagent_thread_ids:
            return json.dumps({"error": "wait_for_subagent 缺少 subagent_thread_ids"}, ensure_ascii=False)

        # 竞态兜底：全部子代理可能已终态（父 spawn 后未立即 wait），直接返回结果，
        # 避免挂起后无人唤醒（终态回调已错过）。resume 时本函数重跑，此处会以
        # 最新 DB 状态短路返回（此时 interrupt() 直接返回 resume 值不再挂起，
        # 但提前返回等价真实结果，语义无损）。
        runtime = get_subagent_runtime()
        results: list[dict] = []
        all_terminal = True
        for sid in subagent_thread_ids:
            instance = await runtime.get_instance(sid)
            if instance is None:
                results.append({"subagent_thread_id": sid, "status": "failed", "result": "子代理不存在"})
                continue
            if instance.status not in (SubAgentStatus.COMPLETED, SubAgentStatus.FAILED):
                all_terminal = False
            results.append(
                {
                    "subagent_thread_id": sid,
                    "status": str(instance.status),
                    "result": instance.result_preview or "",
                }
            )
        if all_terminal:
            return format_subagent_results_text(results)

        # 业务等待挂起：非审批 interrupt，仅暂停流程；由调度器在全部子代理终态时
        # 唤醒（正常路径），或超时清扫任务在 deadline 后唤醒（失败路径）。
        # 返回值（Command(resume) 注入）契约：
        #   {"subagent_results": [{"subagent_thread_id", "status", "result"}, ...]}
        # payload 携带 deadline（epoch 秒）：子代理父图挂起时经
        # langgraph_adapter 写入 SubAgentInstance.pending_interrupt_info，
        # 供超时清扫任务定位过期等待；主图父由进程内 watchdog 兜底。
        resume_value = interrupt(
            {
                "_subagent_wait": True,
                "subagent_thread_ids": subagent_thread_ids,
                "deadline": time.time() + get_wait_timeout_seconds(),
            }
        )

        if isinstance(resume_value, dict) and resume_value.get(SUBAGENT_WAIT_TIMEOUT_FLAG):
            # 超时清扫 resume：走失败路径，逐 id 报告 DB 真实状态。
            # 子代理可能仍在跑（超时唤醒不终止子代理），状态如实返回。
            return await self._report_realtime_states(runtime, subagent_thread_ids, resume_value, timeout=True)

        if not isinstance(resume_value, dict):
            # 非法 resume 值：不伪造失败结果，逐 id 查询真实状态（查无记录报 unknown）。
            logger.warning(
                f"wait_for_subagent 收到非法 resume 值: {subagent_thread_ids}, value={resume_value!r}"
            )
            return await self._report_realtime_states(runtime, subagent_thread_ids, resume_value, timeout=False)

        return format_subagent_results_text(resume_value.get("subagent_results") or [])

    @staticmethod
    async def _report_realtime_states(
        runtime: object,
        subagent_thread_ids: list[str],
        resume_value: object,
        *,
        timeout: bool,
    ) -> str:
        """失败路径（超时/非法 resume）：逐 id 查询 SubAgentInstance 报告真实状态。

        DB 是子代理状态唯一权威：查无记录报 unknown 并附原始 resume 值供排查，
        running 状态如实返回（父 Agent 据此自行决策后续动作）。
        """
        items: list[dict] = []
        for sid in subagent_thread_ids:
            instance = await runtime.get_instance(sid)
            if instance is None:
                items.append(
                    {
                        "subagent_thread_id": sid,
                        "status": "unknown",
                        "result": f"未找到子代理记录（原始 resume 值：{resume_value!r}）",
                    }
                )
            else:
                items.append(
                    {
                        "subagent_thread_id": sid,
                        "status": str(instance.status),
                        "result": instance.result_preview or "",
                    }
                )
        if timeout:
            deadline = resume_value.get("deadline") if isinstance(resume_value, dict) else None
            prefix = f"等待子代理超时（deadline={deadline}），以下为各子代理当前真实状态：\n\n"
        else:
            prefix = f"收到非法 resume 值（{resume_value!r}），以下为各子代理当前真实状态：\n\n"
        return prefix + format_subagent_results_text(items)


wait_for_subagent = WaitForSubAgentTool()
