"""Deep Agent 补丁与韧性执行器模块。

职责：
- ``_PatchCompositeBackend``：修复 deepagents 0.5.x CompositeBackend 的 files_update
  弃用警告，并强制 /sandbox/ 写入守卫。
- ``_get_backend``：根据 backend_type 构建 deepagents 后端（state/filesystem/local_shell）。
- ``_extract_ai_response``：从 create_deep_agent 的 invoke 结果中提取最终 AI 回复。
- ``_DeepAgentExecutor``：Deep Agent 专用执行器，扩展 AgentExecutor 支持 async rebuild
  与多级降级（FULL → REDUCED_TOOLS → NO_TOOLS）。

抽取自原 ``official_deep_agent.py``（Task 18.1）。

依赖关系：
- 本模块不依赖 adapter.py / builders.py，可独立导入。
- 被 ``adapter.py`` 导入：``_DeepAgentExecutor`` / ``_extract_ai_response``。
- 被 ``builders.py`` 导入：``_get_backend``。
"""

import asyncio
import os
from collections.abc import AsyncGenerator, Callable
from typing import Any

from langchain_core.messages import AIMessage

from Django_xm.apps.agent_hub.services.agent_executor import (
    AgentExecutor,
    _HardTimeoutSignaled,
)
from Django_xm.apps.core.config import get_logger
from Django_xm.apps.research.services._constants import (
    SANDBOX_ALLOWED_DIRS as _SANDBOX_ALLOWED_DIRS,
)

logger = get_logger(__name__)


class _PatchCompositeBackend:
    """修复 CompositeBackend 的 files_update 弃用警告 + sandbox 写入守卫

    1. deepagents 0.5.x 的 CompositeBackend.write/edit 使用 dataclasses.replace()
       重建 WriteResult/EditResult，触发 files_update 参数的弃用警告。
       绕过方式：直接修改 path 属性。

    2. /sandbox/ 是 Agent 工具区（skills、MCP 工具、第三方依赖等），
       只允许预定义的工具子目录（_SANDBOX_ALLOWED_DIRS）写入，
       其他 /sandbox/ 路径一律拒绝，引导 Agent 将研究产出写入 /notes/、/plans/、/reports/。
    """

    def __init__(self, composite):
        self._composite = composite

    def __getattr__(self, name):
        return getattr(self._composite, name)

    @staticmethod
    def _check_sandbox_path(file_path: str) -> str | None:
        norm = file_path.replace("\\", "/")
        if not norm.startswith("/sandbox/"):
            return None
        for allowed in _SANDBOX_ALLOWED_DIRS:
            if norm.startswith(allowed):
                return None
        allowed_list = ", ".join(_SANDBOX_ALLOWED_DIRS)
        return (
            f"Error: Path {norm} is not allowed in /sandbox/. "
            f"/sandbox/ is for tool execution only. "
            f"Research output must be written to /notes/, /plans/, or /reports/. "
            f"Allowed sandbox paths: {allowed_list}"
        )

    def _resolve(self, file_path: str):
        backend, key = self._composite._get_backend_and_key(file_path)
        return backend, key

    def write(self, file_path, content):
        err = self._check_sandbox_path(file_path)
        if err:
            from deepagents.backends.protocol import WriteResult

            return WriteResult(error=err, path=None, files_update=None)
        backend, key = self._resolve(file_path)
        res = backend.write(key, content)
        if res.path is not None:
            object.__setattr__(res, "path", file_path)
        return res

    async def awrite(self, file_path, content):
        err = self._check_sandbox_path(file_path)
        if err:
            from deepagents.backends.protocol import WriteResult

            return WriteResult(error=err, path=None, files_update=None)
        backend, key = self._resolve(file_path)
        res = await backend.awrite(key, content)
        if res.path is not None:
            object.__setattr__(res, "path", file_path)
        return res

    def edit(self, file_path, old_string, new_string, replace_all=False):
        err = self._check_sandbox_path(file_path)
        if err:
            from deepagents.backends.protocol import EditResult

            return EditResult(error=err, path=None, files_update=None, occurrences=None)
        backend, key = self._resolve(file_path)
        res = backend.edit(key, old_string, new_string, replace_all=replace_all)
        if res.path is not None:
            object.__setattr__(res, "path", file_path)
        return res

    async def aedit(self, file_path, old_string, new_string, replace_all=False):
        err = self._check_sandbox_path(file_path)
        if err:
            from deepagents.backends.protocol import EditResult

            return EditResult(error=err, path=None, files_update=None, occurrences=None)
        backend, key = self._resolve(file_path)
        res = await backend.aedit(key, old_string, new_string, replace_all=replace_all)
        if res.path is not None:
            object.__setattr__(res, "path", file_path)
        return res


def _get_backend(
    backend_type: str = "state",
    work_dir: str | None = None,
    sandbox_dir: str | None = None,
) -> Any:
    try:
        if backend_type == "state":
            from deepagents.backends import StateBackend

            return StateBackend()
        elif backend_type == "filesystem":
            from deepagents.backends import CompositeBackend, FilesystemBackend

            fs_backend = FilesystemBackend(root_dir=work_dir or ".", virtual_mode=True)

            if sandbox_dir and os.path.isdir(sandbox_dir):
                sandbox_backend = FilesystemBackend(root_dir=sandbox_dir, virtual_mode=True)
                composite = CompositeBackend(
                    default=fs_backend,
                    routes={"/sandbox/": sandbox_backend},
                )
                patched = _PatchCompositeBackend(composite)
                logger.info(f"CompositeBackend: default={work_dir}, /sandbox/={sandbox_dir}")
                return patched

            return fs_backend
        elif backend_type == "local_shell":
            from deepagents.backends import LocalShellBackend

            return LocalShellBackend(workdir=work_dir or ".")
        else:
            logger.warning(f"未知的 backend 类型: {backend_type}，使用 StateBackend")
            from deepagents.backends import StateBackend

            return StateBackend()
    except ImportError as e:
        logger.warning(f"Backend 导入失败: {e}，将不使用 backend")
        return None


def _extract_ai_response(result: dict[str, Any]) -> str:
    """从 create_deep_agent 的 invoke 结果中提取最终 AI 回复"""
    messages = result.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            content = msg.content
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(item.get("text", ""))
                    elif isinstance(item, str):
                        parts.append(item)
                return "\n".join(parts)
            return content
    return ""


class _DeepAgentExecutor(AgentExecutor):
    """Deep Agent 专用执行器：扩展 AgentExecutor 支持 async rebuild 和多级降级。

    相对 AgentExecutor 的差异（Task 18.3）：
    1. run() 使用局部导入的 classify_and_decide / calculate_backoff（兼容测试 patch
       on agent_resilience.classify_and_decide；AgentExecutor 模块级导入不受 patch 影响）
    2. _run_degrade 支持 async _rebuild_with_degraded_tools（原 AgentExecutor 仅支持
       sync rebuild_agent_fn；deep agent 的 rebuild 是 async）
    3. _run_degrade 实现多级降级级联：FULL → REDUCED_TOOLS → NO_TOOLS
       （原 AgentExecutor 仅单级降级，失败后直接 FALLBACK）
    4. _run_fallback 调用 _fallback_direct_answer（原 AgentExecutor 调用
       fallback_service.stream_without_tools；deep agent 的 fallback 是非流式 Dict 返回）

    外层 interrupt 循环（while True + Command(resume=...)）由
    astream_research_with_interrupts 保留，本类仅替换内层 retry/timeout/degrade 循环。
    """

    def __init__(
        self,
        *args,
        rebuild_coro_fn: Any | None = None,
        fallback_direct_answer_fn: Any | None = None,
        deep_agent: Any | None = None,
        query: str | None = None,
        graph_config: dict[str, Any] | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        # async 重建回调：async fn(degraded_tools) -> Optional[graph]
        self._rebuild_coro_fn = rebuild_coro_fn
        # async 回退回调：async fn(query, config) -> Dict
        self._fallback_direct_answer_fn = fallback_direct_answer_fn
        # OfficialDeepAgentAdapter 实例引用（用于更新 self.graph）
        self._deep_agent = deep_agent
        self._query = query
        self._graph_config = graph_config or {}
        # 当前降级级别：None 表示未降级（FULL），降级后设为 REDUCED_TOOLS
        self._current_degradation: Any | None = None

    async def run(
        self,
        loop_fn: Callable,
        agent: Any,
        graph_input: dict,
        config: dict,
        ctx: Any,
        strategy: Any,
        data: dict,
    ) -> AsyncGenerator[dict, None]:
        """带韧性的流式执行（重写以使用局部导入兼容测试 patch）。

        逻辑与 AgentExecutor.run 一致，仅将 classify_and_decide / calculate_backoff
        改为方法内局部导入，使测试 patch（agent_resilience.classify_and_decide）生效。
        """
        # 局部导入，使测试 patch 生效（AgentExecutor 模块级导入不受 patch 影响）
        from langgraph.errors import GraphRecursionError

        from Django_xm.apps.agent_hub.services.agent_resilience import (
            ErrorAction,
            calculate_backoff,
            classify_and_decide,
        )

        self._ctx = ctx
        while ctx.retry_count <= self.config.max_retries:
            try:
                async for event in self._iter_with_timeout(
                    loop_fn,
                    agent,
                    graph_input,
                    config,
                    ctx,
                    strategy,
                    data,
                ):
                    yield event
                return  # 成功

            except _HardTimeoutSignaled:
                # Hard timeout → fallback
                logger.warning(f"[DeepAgentExecutor] 执行超时 (hard): {self.timeout_mgr.elapsed:.1f}s")
                async for fb_event in self._run_fallback():
                    yield fb_event
                return

            except GraphRecursionError:
                # GraphRecursionError → 触发降级（与原 official_deep_agent 行为一致）
                logger.warning("[DeepAgentExecutor] GraphRecursionError，触发降级")
                async for event in self._run_degrade(
                    loop_fn,
                    graph_input,
                    ctx,
                    strategy,
                    data,
                ):
                    yield event
                return

            except Exception as stream_err:
                action, classified = classify_and_decide(
                    stream_err,
                    ctx.retry_count,
                    self.config.max_retries,
                )

                if action == ErrorAction.RETRY:
                    ctx.retry_count += 1
                    backoff = calculate_backoff(ctx.retry_count, self.config)
                    logger.warning(
                        f"[DeepAgentExecutor] 重试 {ctx.retry_count}/{self.config.max_retries}, "
                        f"退避 {backoff:.1f}s: {classified.error_code}"
                    )
                    yield {
                        "type": "retry",
                        "data": {
                            "attempt": ctx.retry_count,
                            "max": self.config.max_retries,
                            "backoff": backoff,
                            "error_code": classified.error_code,
                        },
                    }
                    await asyncio.sleep(backoff)
                    continue

                elif action == ErrorAction.DEGRADE:
                    logger.warning(f"[DeepAgentExecutor] 降级: {classified.error_code}")
                    async for event in self._run_degrade(
                        loop_fn,
                        graph_input,
                        ctx,
                        strategy,
                        data,
                    ):
                        yield event
                    return

                elif action == ErrorAction.FAIL:
                    logger.exception(f"[DeepAgentExecutor] 不可恢复错误: {classified.error_code}: {classified.message}")
                    raise

                else:  # FALLBACK
                    logger.warning(f"[DeepAgentExecutor] 回退: {classified.error_code}")
                    async for fb_event in self._run_fallback():
                        yield fb_event
                    return

    async def _run_degrade(
        self,
        loop_fn: Callable,
        graph_input: dict,
        ctx: Any,
        strategy: Any,
        data: dict,
    ) -> AsyncGenerator[dict, None]:
        """重写 _run_degrade 支持 async rebuild 和多级降级级联。

        级联逻辑（与原 official_deep_agent 一致）：
        - 已降级到 REDUCED_TOOLS → 直接 _run_fallback (NO_TOOLS)
        - FULL → REDUCED_TOOLS: get_degraded_tools + _rebuild_with_degraded_tools
          - 工具集为空或重建失败 → _run_fallback (NO_TOOLS)
          - 重建成功 → 用新 graph 重试
            - 失败 → _run_fallback (NO_TOOLS) [不再次重建]
        """
        # 局部导入，使测试 patch 生效
        from langgraph.errors import GraphRecursionError

        from Django_xm.apps.agent_hub.services.agent_resilience import (
            DegradationLevel,
            get_degraded_tools,
        )

        # 已降级到 REDUCED_TOOLS，再次失败直接回退（多级降级级联）
        if self._current_degradation == DegradationLevel.REDUCED_TOOLS:
            logger.warning("[DeepAgentExecutor] 已降级到 REDUCED_TOOLS，再次失败回退到无工具模式")
            async for fb_event in self._run_fallback():
                yield fb_event
            return

        degraded_tools = get_degraded_tools(self.tools, DegradationLevel.REDUCED_TOOLS)
        if not degraded_tools:
            logger.warning("[DeepAgentExecutor] 降级后无可用工具，回退到无工具模式")
            async for fb_event in self._run_fallback():
                yield fb_event
            return

        logger.info(
            f"[DeepAgentExecutor] 工具降级: {len(self.tools)} → {len(degraded_tools)} 个工具，"
            f"尝试用降级工具重建 Agent 重试"
        )

        # 异步重建 graph（_rebuild_with_degraded_tools 是 async 方法）
        try:
            new_graph = await self._rebuild_coro_fn(degraded_tools)
        except Exception as rebuild_err:
            logger.warning(f"[DeepAgentExecutor] graph 重建异常: {rebuild_err}，回退到无工具模式")
            async for fb_event in self._run_fallback():
                yield fb_event
            return

        if new_graph is None:
            logger.warning("[DeepAgentExecutor] graph 重建失败，回退到无工具模式")
            async for fb_event in self._run_fallback():
                yield fb_event
            return

        # 更新 deep_agent.graph，使 loop_fn 使用新 graph
        self._deep_agent.graph = new_graph
        self._current_degradation = DegradationLevel.REDUCED_TOOLS

        # 重置重复调用检测器，避免降级后的新 agent 受历史记录影响
        self.duplicate_detector.reset()

        # 用新 graph 重试（不再次降级重建，失败则回退）
        try:
            async for event in self._iter_with_timeout(
                loop_fn,
                None,
                graph_input,
                self._graph_config,
                ctx,
                strategy,
                data,
            ):
                yield event
            # 降级成功
            return
        except _HardTimeoutSignaled:
            logger.warning("[DeepAgentExecutor] 降级工具重试超时，回退到无工具模式")
        except GraphRecursionError:
            logger.warning("[DeepAgentExecutor] 降级工具重试达到递归上限，回退到无工具模式")
        except Exception as degrade_err:
            logger.warning(f"[DeepAgentExecutor] 降级工具重试失败: {degrade_err}，回退到无工具模式")

        # 降级失败，落入 FALLBACK
        async for fb_event in self._run_fallback():
            yield fb_event

    async def _run_fallback(self) -> AsyncGenerator[dict, None]:
        """重写 _run_fallback 调用 _fallback_direct_answer。

        与 AgentExecutor._run_fallback 的差异：
        - 不调用 fallback_service.stream_without_tools（流式）
        - 调用 _fallback_direct_answer（非流式，返回 Dict）
        - yield 一个 deep_agent_fallback_result 事件，外层捕获后返回
        """
        self._ctx.fallback_triggered = True
        logger.warning("[DeepAgentExecutor] 回退到无工具直接回答模式")
        try:
            result = await self._fallback_direct_answer_fn(
                self._query,
                self._graph_config,
            )
            yield {"type": "deep_agent_fallback_result", "data": result}
        except Exception as fallback_err:
            logger.exception(f"[DeepAgentExecutor] 无工具回退也失败: {type(fallback_err).__name__}")
            yield {
                "type": "deep_agent_fallback_result",
                "data": {
                    "success": False,
                    "query": self._query,
                    "final_report": None,
                    "error": f"深度研究执行失败: {fallback_err}",
                    "error_code": "FALLBACK_FAILED",
                },
            }
