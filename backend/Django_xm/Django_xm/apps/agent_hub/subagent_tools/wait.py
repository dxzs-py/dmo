"""``wait_for_subagent`` 工具：父 Agent 业务等待挂起（非审批 interrupt）。

事件驱动父唤醒（spec D4）的触发点：
- 父 Agent 调用 ``spawn_sub_agent`` 后拿到 ``subagent_thread_id``；
- 当需要子代理结果推进业务（如撰写报告）时调用本工具；
- 本工具触发业务等待 interrupt（``_subagent_wait``），父 Graph 持久化
  checkpoint 并退出协程（非审批、不产出审批 UI）；
- 子代理终态后由调度器以 ``Command(resume={subagent_thread_id, result})``
  唤醒父 Graph，本工具返回子代理最终结果，父 Agent 继续推进业务。

与 ``spawn_sub_agent`` 分离：spawn 负责派发（立即返回），wait 负责等待结果。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from Django_xm.apps.tools.errors import TOOL_VERSION

logger = logging.getLogger(__name__)

# 业务等待工具名称（与 spawn_sub_agent 分离，父 Agent 用其等待子代理结果）。
WAIT_FOR_SUBAGENT_TOOL_NAME = "wait_for_subagent"


class WaitForSubAgentInput(BaseModel):
    subagent_thread_id: str = Field(
        description="由 spawn_sub_agent 返回的子代理 thread_id，本工具等待其完成并返回结果"
    )


class WaitForSubAgentTool(BaseTool):
    name: str = WAIT_FOR_SUBAGENT_TOOL_NAME
    version: str = TOOL_VERSION
    metadata: dict = Field(
        default_factory=lambda: {"tier": "extended", "visibility": "selectable", "category": "agent"}
    )
    description: str = (
        "等待指定子代理完成并返回其最终结果。调用后当前代理会挂起（保存进度），"
        "待该子代理执行完毕（成功或失败）后自动恢复并拿到结果。"
        "使用时机：已通过 spawn_sub_agent 派发子代理、且后续步骤需要其产出时调用。"
        "参数：subagent_thread_id-由 spawn_sub_agent 返回的子代理 thread_id（必填）。"
    )
    args_schema: type[BaseModel] = WaitForSubAgentInput

    def _run(self, subagent_thread_id: str) -> str:
        return "wait_for_subagent 需要异步执行，请通过 ainvoke 调用"

    async def _arun(self, subagent_thread_id: str) -> str:
        """触发业务等待挂起（interrupt），并在调度器唤醒后返回子代理结果。"""
        from langgraph.types import interrupt

        from Django_xm.apps.ai_engine.models import SubAgentStatus
        from Django_xm.apps.ai_engine.subagent_runtime import get_subagent_runtime

        # 竞态兜底：子代理可能已终态（父 spawn 后未立即 wait），直接返回结果，
        # 避免挂起后无人唤醒（终态回调已错过）。
        instance = await get_subagent_runtime().get_instance(subagent_thread_id)
        if instance is not None and instance.status in (SubAgentStatus.COMPLETED, SubAgentStatus.FAILED):
            return self._format_resume_value(
                {
                    "subagent_thread_id": subagent_thread_id,
                    "status": instance.status,
                    "result": instance.result_preview or "",
                }
            )

        # 业务等待挂起：非审批 interrupt，仅暂停流程；由调度器在子代理终态时唤醒。
        # 返回值（Command(resume) 注入）契约：
        #   {"subagent_thread_id": str, "status": "completed"|"failed", "result": str}
        resume_value = interrupt(
            {
                "_subagent_wait": True,
                "subagent_thread_id": subagent_thread_id,
            }
        )

        if not isinstance(resume_value, dict):
            logger.warning(
                f"wait_for_subagent 收到非法 resume 值: {subagent_thread_id}, value={resume_value!r}"
            )
            return json.dumps(
                {"subagent_thread_id": subagent_thread_id, "status": "failed", "result": "子代理结果缺失"},
                ensure_ascii=False,
            )

        return self._format_resume_value(resume_value)

    @staticmethod
    def _format_resume_value(resume_value: dict) -> str:
        """将 resume 值格式化为父 Agent 可读的结果文本。"""
        subagent_thread_id = resume_value.get("subagent_thread_id", "")
        status = resume_value.get("status", "failed")
        result = resume_value.get("result", "") or ""
        if status == "completed":
            return f"子代理 {subagent_thread_id} 已完成。结果：\n{result}"
        return f"子代理 {subagent_thread_id} 执行失败：\n{result or '未知错误'}"


wait_for_subagent = WaitForSubAgentTool()
