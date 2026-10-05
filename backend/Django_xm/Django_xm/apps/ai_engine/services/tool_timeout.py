"""通用工具级执行超时（B6/P2-13 不对齐项②）。

现状：单个工具执行无统一时长上限——web_fetch 自带 30s、shell_exec 上限 300s、
MCP 加载 15/50s，但工具整体执行路径（ToolNode → BaseTool.ainvoke）缺省无界，
慢/挂死工具仅受 Agent 全局 soft/hard 超时约束，且硬超时只在事件间隙检查，
单个无界工具会无限推迟 hard_timeout 判定。

实现：经 langchain 官方 middleware 扩展点 ``wrap_tool_call`` /
``awrap_tool_call`` 包裹工具执行（与项目内 RateLimitMiddleware /
SubAgentToolEventMiddleware 同一扩展点），对单次工具调用施加
``asyncio.wait_for`` 超时。超时不抛异常中断执行流，而是返回 error
ToolMessage 让 agent 感知失败并自行调整（与 shell_exec 超时返回语义一致）。

默认值取 300s 量级的依据：
- 与最慢现役工具上限对齐（shell_exec MAX_TIMEOUT=300）；
- 远小于 Celery rag 任务 soft_time_limit=1500s，单工具不会吃满任务预算；
- 覆盖现有单工具超时最大值（web_fetch 30s / MCP 远程 50s）的合理余量。
生产可经 Django settings 覆盖：AGENT_TOOL_EXECUTION_TIMEOUT（秒）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)

# 通用工具级缺省执行超时（秒）。settings.AGENT_TOOL_EXECUTION_TIMEOUT 可覆盖；
# 显式设 None 表示不限制（不推荐，会退回无界工具执行）。
DEFAULT_TOOL_EXECUTION_TIMEOUT: float = 300.0


def get_tool_execution_timeout() -> float | None:
    """读取工具级执行超时（settings 覆盖 → 代码默认）。"""
    try:
        from django.conf import settings

        return getattr(settings, "AGENT_TOOL_EXECUTION_TIMEOUT", DEFAULT_TOOL_EXECUTION_TIMEOUT)
    except Exception:
        return DEFAULT_TOOL_EXECUTION_TIMEOUT


class ToolExecutionTimeoutMiddleware(AgentMiddleware):
    """单次工具调用缺省超时中间件（B6/P2-13）。

    仅实施异步路径（``awrap_tool_call``）：项目全部 agent 执行链
    （chat/research/learning）均走 astream 异步流。同步 ``wrap_tool_call``
    不覆写（基类默认透传）——同步线程无法被 wait_for 安全终止，
    且现役同步 invoke 仅存在于测试场景。

    超时行为：返回 error ToolMessage（不抛异常），agent 收到失败反馈后
    可换工具/改参数继续；执行流不中断，与 hard_timeout 的强杀语义分层。
    """

    def __init__(self, timeout: float | None = None):
        self.timeout = timeout if timeout is not None else get_tool_execution_timeout()

    @staticmethod
    def _tool_call_id(request: ToolCallRequest) -> str:
        return request.tool_call.get("id", "") if isinstance(request.tool_call, dict) else ""

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        tool_name = request.tool_call.get("name", "unknown") if isinstance(request.tool_call, dict) else "unknown"
        if self.timeout is None:
            return await handler(request)
        try:
            return await asyncio.wait_for(handler(request), timeout=self.timeout)
        except TimeoutError:
            call_id = self._tool_call_id(request)
            logger.warning(
                f"[ToolTimeout] 工具执行超时 ({self.timeout}s): tool={tool_name}, call_id={call_id}"
            )
            return ToolMessage(
                content=f"工具 {tool_name} 执行超时（{self.timeout:g} 秒），已终止。"
                "请调整参数重试、换用其他工具，或基于已有信息继续。",
                tool_call_id=call_id,
            )
