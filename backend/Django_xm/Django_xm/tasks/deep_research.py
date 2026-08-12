"""
深度研究 Celery 任务
将原本的 threading 后台任务迁移到 Celery 任务队列
"""

import asyncio
import logging
import os
import shutil
import time
from typing import Any

from celery import shared_task
from celery.exceptions import Retry
from django.db import models

from Django_xm.apps.agent_hub import AgentConfig, AgentType
from Django_xm.apps.agent_hub import create as agent_hub_create
from Django_xm.apps.knowledge.services.multi_kb_retriever import build_retriever_tool_for_research
from Django_xm.apps.research.services.research_runner import execute_research_async, finalize_research
from Django_xm.apps.research.services.task_manager import update_task_status
from Django_xm.tasks.base import TrackedTask

logger = logging.getLogger(__name__)

# 动态构建可自动重试的异常列表
_RETRYABLE_EXCEPTIONS = [ConnectionError, TimeoutError, OSError]
try:
    from openai import APITimeoutError as _OpenAIAPITimeoutError
    from openai import RateLimitError as _OpenAIRateLimitError

    _RETRYABLE_EXCEPTIONS.extend([_OpenAIRateLimitError, _OpenAIAPITimeoutError])
except ImportError:
    pass
try:
    from httpx import ConnectTimeout as _HttpxConnectTimeout
    from httpx import ReadTimeout as _HttpxReadTimeout

    _RETRYABLE_EXCEPTIONS.extend([_HttpxConnectTimeout, _HttpxReadTimeout])
except ImportError:
    pass


def _publish_research_failure(thread_id: str, error_message: str) -> None:
    """发布深度研究失败结果到 Redis（供聊天 SSE 及时结束等待）。

    聊天模块深度研究模式的 SSE 生成器订阅 research:result:{thread_id}
    通道等待研究结果；若终态失败不发布失败结果，SSE 将一直阻塞到
    超时（前端输入框持续显示运行中）。仅在终态失败时调用，可重试
    异常或等待审批（interrupted）路径不得调用。

    Args:
        thread_id: 研究任务 ID
        error_message: 失败信息（作为 final_report / error 字段）
    """
    try:
        from Django_xm.apps.research.services.research_runner import publish_result_payload

        publish_result_payload(
            thread_id,
            {
                "response_time": 0,
                "success": False,
                "final_report": error_message,
                "files": None,
                "usage_data": None,
                "error": error_message,
            },
        )
    except Exception as e:
        logger.warning(f"发布研究失败结果到 Redis 失败: {e}")


@shared_task(
    bind=True,
    name="research.run_research",
    max_retries=2,
    default_retry_delay=60,
    soft_time_limit=1800,
    autoretry_for=tuple(_RETRYABLE_EXCEPTIONS),
    retry_backoff=True,
    retry_backoff_max=60,
)
def run_research_task(
    self,
    thread_id: str,
    query: str,
    enable_web_search: bool = True,
    enable_doc_analysis: bool = False,
    knowledge_base_ids: list | None = None,
    user_id: int | None = None,
    use_mcp: bool = False,
    selected_mcp_servers: list | None = None,
    selected_tools: list | None = None,
    provider_id: str | None = None,
    model_name: str | None = None,
    enable_deep_thinking: bool = False,
    temperature: float | None = None,
    max_tokens: int | None = None,
    special_params: dict | None = None,
    continue_task_id: str | None = None,
    publish_to_redis: bool = False,
    session_id: str | None = None,
):
    tracker = TrackedTask(self)
    if user_id:
        tracker.set_created_by(user_id)
    tracker.set_task_type("deep_research")
    tracker.set_task_manager_id(thread_id, sync_fn=update_task_status)

    start_time = time.time()

    def _mark_failed(error_msg: str, exc: Exception | None = None):
        tracker.mark_failure(error_message=error_msg)
        if exc and self.request.retries < self.max_retries:
            raise self.retry(exc=exc)

    try:
        logger.info(f"[Celery] 深度研究任务开始：{thread_id}")
        tracker.mark_started()
        tracker.update_progress(10, "研究任务启动")

        # 预热缓存，避免异步上下文中反复回退到 config.py 默认值
        try:
            from Django_xm.apps.ai_engine.models import warmup_system_config_cache
            from Django_xm.apps.ai_engine.services.registry_service import warmup_cache

            warmup_cache()
            warmup_system_config_cache()
        except Exception as e:
            logger.debug(f"缓存预热失败（非致命）: {e}")

        retriever_tool = None
        if enable_doc_analysis and knowledge_base_ids and user_id:
            logger.info(f"[Celery] 构建多知识库检索工具: {knowledge_base_ids}")
            try:
                retriever_tool = build_retriever_tool_for_research(
                    knowledge_base_ids=knowledge_base_ids,
                    user_id=user_id,
                )
                if retriever_tool:
                    logger.info("[Celery] 知识库检索工具创建成功")
                else:
                    logger.warning("[Celery] 知识库检索工具创建失败，文档分析将无法检索")
            except Exception:
                logger.exception("[Celery] 构建知识库检索工具异常")

        extra_tools = []
        if use_mcp or selected_tools:
            try:
                from Django_xm.apps.ai_engine.capabilities import registry

                capabilities = registry.get_default_capabilities("deep_research")

                if "tool_injection" in capabilities:
                    tool_config = {
                        "use_tools": True,
                        "use_web_search": False,
                        "use_mcp": use_mcp,
                        "selected_tools": selected_tools,
                        "selected_mcp_servers": selected_mcp_servers,
                        "user_id": user_id,
                        "tool_tier": "extended",
                    }
                    extra_tools = asyncio.run(
                        registry.build_tools_for_agent_async(
                            "deep_research",
                            capabilities,
                            tool_config=tool_config,
                        )
                    )
                    if extra_tools:
                        logger.info(f"[Celery] 通过 CapabilityRegistry 加载 {len(extra_tools)} 个额外工具")
                else:
                    from Django_xm.apps.tools import get_tools_for_request_async

                    extra_tools = asyncio.run(
                        get_tools_for_request_async(
                            use_tools=True,
                            use_web_search=False,
                            use_mcp=use_mcp,
                            selected_mcp_servers=selected_mcp_servers,
                            selected_tools=selected_tools,
                            user_id=user_id,
                            tool_tier="extended",
                        )
                    )
                    if extra_tools:
                        logger.info(f"[Celery] 回退直接加载 {len(extra_tools)} 个额外工具")
            except Exception as e:
                logger.warning(f"[Celery] 加载额外工具失败: {e}")

        selected_skill_names = None
        if selected_tools:
            selected_skill_names = [
                name.replace("skill_", "", 1) for name in selected_tools if name.startswith("skill_")
            ]

        research_context = ""
        if continue_task_id:
            from Django_xm.apps.research.services.research_runner import load_research_context

            ctx = load_research_context(continue_task_id)
            if ctx:
                research_context += (
                    "## 先前研究成果摘要\n\n"
                    "以下是之前研究的成果，请在此基础上继续深入研究。\n"
                    "**要求**：\n"
                    "1. 不要重复已有内容，聚焦于未覆盖的方面\n"
                    "2. 对已有结论进行补充证据和深入分析\n"
                    "3. 将新的研究发现写入新的文件（不要覆盖已有文件）\n"
                    "4. 在最终报告中整合新旧研究成果\n"
                    "5. 父任务的原始文件已放在 /sandbox/inherited/ 目录下，可用 read_file 读取参考\n\n"
                    f"{ctx}"
                )
            logger.info(f"[Celery] 续研模式：注入父任务上下文，thread_id={continue_task_id}")

            # 继承父任务的文件到 sandbox 目录（供 agent 读取，不污染用户可见的研究目录）
            try:
                from django.conf import settings as django_settings

                data_dir = str(
                    getattr(django_settings, "DATA_DIR", None)
                    or os.path.join(
                        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data"
                    )
                )
                parent_work_dir = os.path.join(data_dir, "research", continue_task_id)
                child_work_dir = os.path.join(data_dir, "research", thread_id)
                sandbox_dir = os.path.join(child_work_dir, "sandbox")
                os.makedirs(sandbox_dir, exist_ok=True)
                if os.path.isdir(parent_work_dir):
                    inherited_dir = os.path.join(sandbox_dir, "inherited")
                    os.makedirs(inherited_dir, exist_ok=True)
                    for subdir in ("notes", "plans", "reports"):
                        src = os.path.join(parent_work_dir, subdir)
                        if os.path.isdir(src):
                            dst = os.path.join(inherited_dir, subdir)
                            os.makedirs(dst, exist_ok=True)
                            for fname in os.listdir(src):
                                src_file = os.path.join(src, fname)
                                dst_file = os.path.join(dst, fname)
                                if os.path.isfile(src_file) and not os.path.exists(dst_file):
                                    shutil.copy2(src_file, dst_file)
                    logger.info(f"[Celery] 已继承父任务文件到 sandbox: {parent_work_dir} -> {inherited_dir}")
            except Exception as e:
                logger.warning(f"[Celery] 继承父任务文件失败: {e}")

        tools = []
        if retriever_tool:
            tools.append(retriever_tool)
        if extra_tools:
            tools.extend(extra_tools)

        tool_config = {
            "use_tools": True,
            "use_web_search": enable_web_search,
            "use_doc_analysis": enable_doc_analysis,
            "user_id": user_id,
        }

        skills = None
        if selected_skill_names:
            try:
                from Django_xm.apps.tools.skills.adapter import SkillAdapter

                adapter = SkillAdapter(user_id=user_id)
                skill_dirs = adapter.to_deep_agent_skills(selected_skill_names=selected_skill_names)
                if skill_dirs:
                    skills = skill_dirs
            except Exception as e:
                logger.warning(f"[Celery] 加载 Skill 目录失败: {e}")

        system_prompt = None
        if research_context:
            # 续研模式：使用 DeepAgent 完整的系统提示词作为基础，追加续研上下文
            # 不能用 get_system_prompt("deep-research")，那个是 yaml 中的弱化版，缺少关键指令
            from Django_xm.apps.agent_hub.builders.deep_builder import DEEP_RESEARCH_SYSTEM_PROMPT

            system_prompt = f"{DEEP_RESEARCH_SYSTEM_PROMPT}\n\n---\n\n{research_context}"
            logger.info(f"[Celery] 续研 system_prompt 已构建 ({len(system_prompt)} 字符)")

        config = AgentConfig(
            agent_type=AgentType.DEEP_RESEARCH,
            provider_id=provider_id,
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            special_params=special_params,
            enable_deep_thinking=enable_deep_thinking,
            tools=tools if tools else None,
            tool_config=tool_config,
            system_prompt=system_prompt,
            user_id=user_id,
            session_id=thread_id,
            skills=skills,
            debug=False,
        )

        # 在同一个事件循环中创建 agent 和执行研究，
        # 避免 asyncio.run() 关闭事件循环后 checkpointer 连接失效
        async def _create_and_run():
            # 深度研究使用 astream()，需要异步 Checkpointer
            from Django_xm.apps.ai_engine.services.checkpointer_factory import get_async_checkpointer

            async_cp = await get_async_checkpointer()
            if async_cp is not None:
                config.checkpointer = async_cp
                logger.info("[Celery] 深度研究使用异步 Checkpointer")

            # agent_hub_create 对 DEEP_RESEARCH 类型返回 OfficialDeepAgentAdapter
            # （DeepAgentBuilder.build() 直接构造 adapter，注入 original_tools / original_config / model，
            #  韧性降级 _rebuild_with_degraded_tools 可用）
            agent = await agent_hub_create(config)

            result = await execute_research_async(
                agent, query, thread_id,
                disable_llm_cache=True,
                user_id=user_id,
                chat_session_id=session_id,
            )

            # 释放异步 Checkpointer 连接
            try:
                from Django_xm.apps.ai_engine.services.checkpointer_factory import release_async_checkpointer

                await release_async_checkpointer()
            except Exception:  # noqa: S110  # cleanup, checkpointer 资源释放失败可忽略
                pass

            return result

        result = asyncio.run(_create_and_run())
        tracker.update_progress(30, "智能体初始化完成")

        response_time = round(time.time() - start_time, 2)
        tracker.update_progress(90, "研究执行完成")

        if not result.success:
            if result.error_message == "interrupted":
                # Agent 正常中断等待审批（Path D 退出），非失败
                # 审批记录已在 on_interrupt 回调中创建
                # 状态保持 pending：前端根据 pending 状态 + 存在待审批记录计算显示文案
                logger.info(f"[Celery] 研究中断等待审批：{thread_id}")
                update_task_status(thread_id, {"status": "awaiting_approval"})
                return {"status": "interrupted", "thread_id": thread_id, "message": "等待用户审批"}
            logger.warning(f"[Celery] 研究逻辑失败：{thread_id}, {result.error_message}")
            if publish_to_redis:
                _publish_research_failure(thread_id, result.error_message)
            _mark_failed(result.error_message)
            return {"status": "error", "thread_id": thread_id, "error": result.error_message}

        finalize_research(thread_id, result, response_time, sync_files=True, publish_to_redis=publish_to_redis)

        # 回写 ChatMessage + 广播 stream_completed，确保前端感知完成
        try:
            from Django_xm.apps.research.models import ResearchTask
            from Django_xm.apps.research.services.writeback import (
                writeback_to_chat_message,
                broadcast_stream_completed,
            )
            research_task = ResearchTask.objects.get(task_id=thread_id)
            chat_session_id = research_task.session_id
            if chat_session_id:
                message_id = writeback_to_chat_message(
                    thread_id, result.final_report, success=True,
                    chat_session_id=chat_session_id,
                )
                broadcast_stream_completed(
                    chat_session_id, thread_id, success=True,
                    final_report=result.final_report, message_id=message_id,
                    user_id=research_task.created_by_id,
                )
        except Exception as e:
            logger.warning(f"[Celery] 回写 ChatMessage 失败: {e}")

        cleanup_research_sandbox.apply_async(args=(thread_id,), countdown=120)

        tracker.mark_success(
            result={
                "thread_id": thread_id,
                "final_report": result.final_report,
            }
        )
        logger.info(f"[Celery] 深度研究任务完成：{thread_id}")
        return {"status": "success", "thread_id": thread_id}

    except Retry:
        raise
    except Exception as exc:
        logger.exception(f"[Celery] 深度研究任务失败：{thread_id}, 错误：")
        try:
            _mark_failed(str(exc), exc)
        except Retry:
            # 可重试异常且剩余重试次数：不发布失败结果，避免聊天 SSE 提前结束
            raise
        # 终态失败：发布失败结果，让聊天 SSE 及时结束
        if publish_to_redis:
            _publish_research_failure(thread_id, str(exc))
        # 回写 ChatMessage + 广播 stream_completed，确保前端感知失败
        try:
            from Django_xm.apps.research.models import ResearchTask
            from Django_xm.apps.research.services.writeback import (
                writeback_to_chat_message,
                broadcast_stream_completed,
            )
            research_task = ResearchTask.objects.get(task_id=thread_id)
            chat_session_id = research_task.session_id
            if chat_session_id:
                message_id = writeback_to_chat_message(
                    thread_id, str(exc), success=False,
                    chat_session_id=chat_session_id,
                )
                broadcast_stream_completed(
                    chat_session_id, thread_id, success=False,
                    error=str(exc), message_id=message_id,
                    user_id=research_task.created_by_id,
                )
        except Exception as e:
            logger.warning(f"[Celery] 回写 ChatMessage 失败: {e}")
        cleanup_research_sandbox.apply_async(args=(thread_id,), countdown=120)
        return {"status": "error", "thread_id": thread_id, "error": str(exc)}


@shared_task(
    name="research.cleanup_sandbox",
    max_retries=1,
    soft_time_limit=60,
    autoretry_for=tuple(_RETRYABLE_EXCEPTIONS),
    retry_backoff=True,
    retry_backoff_max=60,
)
def cleanup_research_sandbox(thread_id: str):
    """研究完成后直接删除 sandbox 目录（含 skill 工具等临时资源）

    注意：此操作不经过 cross_app 的"双方都删才清理"守卫，因为：
    - sandbox 仅含临时工具文件（如 skill 脚本），不含用户可见的研究产出
    - 研究产出（notes/plans/reports）存储在 research/{thread_id}/ 根目录，由守卫保护
    - 研究完成后 sandbox 已无用途，延迟 120 秒清理是预期行为
    """
    try:
        from django.conf import settings as django_settings

        data_dir = str(
            getattr(django_settings, "DATA_DIR", None)
            or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
        )
        sandbox_dir = os.path.join(data_dir, "research", thread_id, "sandbox")

        if not os.path.isdir(sandbox_dir):
            return {"status": "skipped", "reason": "sandbox not found"}

        shutil.rmtree(sandbox_dir, ignore_errors=True)
        logger.info(f"[Celery] 已清理 sandbox 目录: {sandbox_dir}")
        return {"status": "success", "cleaned": sandbox_dir}
    except Exception as e:
        logger.exception("[Celery] 清理 sandbox 失败")
        return {"status": "error", "error": str(e)}


# ---------------------------------------------------------------------------
# Path D: 审批恢复任务
# ---------------------------------------------------------------------------


async def _build_research_agent_from_task(task):
    """从 ResearchTask 模型重建研究智能体（供 research_resume_task 复用）。

    从 DB 存储的字段重建与初始任务相同配置的 agent：
    - retriever_tool（文档分析 + 知识库）
    - extra_tools（MCP / 自定义工具）
    - skills（Skill 适配器）
    - AgentConfig（model / tools / tool_config / session_id 等）

    Args:
        task: ResearchTask 模型实例

    Returns:
        构建完成的 agent（OfficialDeepAgentAdapter）
    """
    user_id = task.created_by_id if task.created_by_id else None

    # 1. 构建知识库检索工具
    retriever_tool = None
    if task.enable_doc_analysis and task.knowledge_base_ids and user_id:
        logger.info(f"[Resume] 构建多知识库检索工具: {task.knowledge_base_ids}")
        try:
            retriever_tool = build_retriever_tool_for_research(
                knowledge_base_ids=task.knowledge_base_ids,
                user_id=user_id,
            )
        except Exception:
            logger.exception("[Resume] 构建知识库检索工具异常")

    # 2. 加载额外工具（MCP / 自定义工具）
    extra_tools = []
    if task.use_mcp or task.selected_tools:
        try:
            from Django_xm.apps.ai_engine.capabilities import registry

            capabilities = registry.get_default_capabilities("deep_research")

            if "tool_injection" in capabilities:
                tool_config = {
                    "use_tools": True,
                    "use_web_search": False,
                    "use_mcp": task.use_mcp,
                    "selected_tools": task.selected_tools,
                    "selected_mcp_servers": task.selected_mcp_servers,
                    "user_id": user_id,
                    "tool_tier": "extended",
                }
                extra_tools = await registry.build_tools_for_agent_async(
                    "deep_research",
                    capabilities,
                    tool_config=tool_config,
                )
            else:
                from Django_xm.apps.tools import get_tools_for_request_async

                extra_tools = await get_tools_for_request_async(
                    use_tools=True,
                    use_web_search=False,
                    use_mcp=task.use_mcp,
                    selected_mcp_servers=task.selected_mcp_servers,
                    selected_tools=task.selected_tools,
                    user_id=user_id,
                    tool_tier="extended",
                )
            if extra_tools:
                logger.info(f"[Resume] 加载 {len(extra_tools)} 个额外工具")
        except Exception as e:
            logger.warning(f"[Resume] 加载额外工具失败: {e}")

    # 3. 构建 Skill 适配器
    skills = None
    if task.selected_tools:
        selected_skill_names = [
            name.replace("skill_", "", 1) for name in task.selected_tools if name.startswith("skill_")
        ]
        if selected_skill_names:
            try:
                from Django_xm.apps.tools.skills.adapter import SkillAdapter

                adapter = SkillAdapter(user_id=user_id)
                skill_dirs = adapter.to_deep_agent_skills(selected_skill_names=selected_skill_names)
                if skill_dirs:
                    skills = skill_dirs
            except Exception as e:
                logger.warning(f"[Resume] 加载 Skill 目录失败: {e}")

    # 4. 组装工具列表
    tools = []
    if retriever_tool:
        tools.append(retriever_tool)
    if extra_tools:
        tools.extend(extra_tools)

    # 5. 构建 AgentConfig
    tool_config = {
        "use_tools": True,
        "use_web_search": task.enable_web_search,
        "use_doc_analysis": task.enable_doc_analysis,
        "user_id": user_id,
    }

    config = AgentConfig(
        agent_type=AgentType.DEEP_RESEARCH,
        model_name=task.model or None,
        tools=tools if tools else None,
        tool_config=tool_config,
        system_prompt=None,  # 恢复时不需要 system_prompt（checkpoint 已有上下文）
        user_id=user_id,
        session_id=task.task_id,
        skills=skills,
        debug=False,
    )

    # 6. 注入异步 Checkpointer（与初始任务一致）
    from Django_xm.apps.ai_engine.services.checkpointer_factory import get_async_checkpointer

    async_cp = await get_async_checkpointer()
    if async_cp is not None:
        config.checkpointer = async_cp
        logger.info("[Resume] 使用异步 Checkpointer")

    # 7. 创建 agent
    agent = await agent_hub_create(config)
    return agent


def _finalize_batch_approvals(all_resume_values: dict, thread_id: str) -> None:
    """审批终态化：对批次内所有已确认的审批记录调用 complete_approval。

    修复 DB 残留 pending 污染后续批次计数的 bug：
    research_resume_task 从未调用 complete_approval → 审批通过后
    Approval DB 记录残留 pending → 后续超时批次误含已通过的审批（batch_size 错误）。

    all_resume_values 可能是两种格式：
    1. {tool_call_id: bool}（单层，无 graph_interrupt_id 分隔）
    2. {langgraph_resume_id: {tool_call_id: bool}}（双层，批量场景）

    Args:
        all_resume_values: 同批次所有审批决策
        thread_id: 研究任务 ID（用于日志）
    """
    from Django_xm.apps.approvals.models import Approval
    from Django_xm.apps.approvals.services.approval_service import complete_approval

    # 展平决策：收集所有 tool_call_id → bool
    flat_decisions: dict[str, bool] = {}
    for key, val in all_resume_values.items():
        if isinstance(val, dict):
            # 双层格式：{langgraph_resume_id: {tool_call_id: bool}}
            flat_decisions.update(val)
        elif isinstance(val, bool):
            # 单层格式：{tool_call_id: bool}
            flat_decisions[key] = val

    # 终态化所有已决断的审批（包括确认/拒绝/超时）
    for tc_or_int_id, decision in flat_decisions.items():
        try:
            # 按 tool_call_id 或 interrupt_id 匹配 Approval 记录
            approval_qs = Approval.objects.filter(
                source=Approval.SOURCE_DEEP_RESEARCH,
                source_id=thread_id,
            )
            approval = approval_qs.filter(
                models.Q(interrupt_id=tc_or_int_id)
                | models.Q(extra__tool_call_id=tc_or_int_id)
            ).first()

            if approval is None:
                logger.debug(
                    f"[Resume] 审批终态化跳过(未找到记录): "
                    f"tc_id={tc_or_int_id}, task={thread_id}"
                )
                continue

            # 只终态化状态为 processing 的记录（已被 timeout_approval 设为 processing）
            # 或状态为 waiting 的记录（用户手动确认后进入的等待状态）
            if approval.state in (Approval.STATE_PROCESSING, Approval.STATE_WAITING):
                final_state = Approval.STATE_APPROVED if decision is True else Approval.STATE_REJECTED
                complete_approval(approval.interrupt_id, final_state)
                logger.info(
                    f"[Resume] 审批终态化: tc_id={tc_or_int_id}, "
                    f"task={thread_id}, state={final_state}"
                )
        except Exception:
            logger.warning(
                f"[Resume] 审批终态化失败: tc_id={tc_or_int_id}, task={thread_id}",
                exc_info=True,
            )


def _collect_batch_decisions(thread_id: str, graph_interrupt_id: str) -> tuple[dict, bool]:
    """收集同批次所有审批决策。

    批量 interrupt 场景：一个 interrupt 包含多个工具审批，
    所有工具审批完成后才能恢复 agent。

    Args:
        thread_id: 研究任务 ID
        graph_interrupt_id: 批次 ID

    Returns:
        (resume_by_interrupt, all_resolved)
        - resume_by_interrupt: {langgraph_resume_id: {tool_call_id: bool}}
          Command(resume=...) 的 key 必须是 LangGraph Interrupt.id（langgraph_resume_id）
        - all_resolved: 是否所有审批都已决断（approved/rejected/timeout）
    """
    from Django_xm.apps.approvals.models import Approval

    batch_approvals = Approval.objects.filter(
        source=Approval.SOURCE_DEEP_RESEARCH,
        source_id=thread_id,
        extra__graph_interrupt_id=graph_interrupt_id,
    )

    # 按 langgraph_resume_id 分组，确保 Command(resume=...) 的 key 是 LangGraph 的 intr.id
    # 参见 LangGraph 文档：多 pending interrupt 时必须指定 interrupt id
    resume_by_interrupt = {}
    all_resolved = True

    for approval in batch_approvals:
        extra = approval.extra if isinstance(approval.extra, dict) else {}
        tc_id = extra.get("tool_call_id", approval.interrupt_id)
        langgraph_id = extra.get("langgraph_resume_id", graph_interrupt_id)

        if approval.state in (Approval.STATE_APPROVED, Approval.STATE_PROCESSING):
            # processing 表示用户已确认、审批正在执行恢复，等同于 approved
            resume_by_interrupt.setdefault(langgraph_id, {})[tc_id] = True
        elif approval.state == Approval.STATE_REJECTED:
            resume_by_interrupt.setdefault(langgraph_id, {})[tc_id] = False
        elif approval.state == Approval.STATE_TIMEOUT:
            resume_by_interrupt.setdefault(langgraph_id, {})[tc_id] = False  # 超时视为拒绝
        elif approval.state == Approval.STATE_WAITING:
            # waiting 表示同批次其他工具还在等待，本工具已确认
            resume_by_interrupt.setdefault(langgraph_id, {})[tc_id] = True
        else:
            # pending / unknown → 未决断
            all_resolved = False

    return resume_by_interrupt, all_resolved


@shared_task(
    bind=True,
    name="research.resume",
    max_retries=10,
    retry_backoff=True,
    retry_backoff_max=30,
    soft_time_limit=1800,
)
def research_resume_task(
    self,
    thread_id: str,
    interrupt_id: str,
    langgraph_resume_id: str,
    graph_interrupt_id: str,
    resume_value,
    user_id: int | None = None,
    message_id: str = "",
    chat_session_id: str | None = None,
):
    """深度研究审批恢复任务（Path D）。

    用户审批后，从 checkpoint 恢复 agent 执行：
    1. 检查同批次所有审批是否已决断
    2. 若未全部决断 → 退出（等待最后一个审批触发恢复）
    3. 若已全部决断 → 收集决策 → 构建 agent → Command(resume=...) 恢复
    4. 执行过程中若再次 interrupt → 创建新 Approval，退出（等待新恢复任务）

    并发安全：
        使用 Redis SET NX 锁确保同一批次只有一个任务执行恢复。

    Args:
        thread_id: 研究任务 ID（= approval.source_id）
        interrupt_id: 当前审批的 interrupt_id（= tool_call_id）
        langgraph_resume_id: LangGraph Interrupt.id（Command resume 的 KEY）
        graph_interrupt_id: 批次 UUID
        resume_value: 当前审批的用户决策（True/False 或 dict）
        user_id: 用户 ID
        message_id: 关联 chat message ID
        chat_session_id: 关联 chat session ID（跨模块同步）
    """
    tracker = TrackedTask(self)
    if user_id:
        tracker.set_created_by(user_id)
    tracker.set_task_type("deep_research")
    tracker.set_task_manager_id(thread_id, sync_fn=update_task_status)

    start_time = time.time()

    try:
        logger.info(
            f"[Resume] 深度研究审批恢复开始: thread_id={thread_id}, "
            f"interrupt_id={interrupt_id}, graph_interrupt_id={graph_interrupt_id}"
        )

        # 1. 并发安全：thread 粒度恢复锁
        # 同一 thread 的多个审批（可能来自并行恢复任务）会同时触发 research_resume_task，
        # 必须保证同一 thread 只有一个恢复任务执行，防止 checkpoint 竞争/重复审批。
        # 锁覆盖整个恢复执行周期，TTL 1800s（与审批 TTL 一致）。
        from django.core.cache import cache

        thread_lock_key = f"research:resume:task_lock:{thread_id}"
        if not cache.add(thread_lock_key, "1", timeout=1800):
            logger.info(f"[Resume] 同 thread 恢复任务执行中，延时重试: thread_id={thread_id}")
            raise self.retry(
                countdown=15,
                exc=RuntimeError(f"thread_resume_lock held: {thread_id}"),
            )

        # 1.1 批次恢复锁（graph_interrupt_id 粒度）
        # 同一批次的多个审批可能同时触发 research_resume_task，
        # 只有第一个获取锁的任务执行恢复，其余退出。
        lock_key = f"research:resume:lock:{graph_interrupt_id}"
        # SET NX + EX 300：锁有效期 5 分钟（足够恢复执行）
        lock_acquired = cache.add(lock_key, "1", timeout=300)
        if not lock_acquired:
            logger.info(f"[Resume] 同批次恢复锁已被占用，跳过: graph_interrupt_id={graph_interrupt_id}")
            try:
                cache.delete(thread_lock_key)
            except Exception:  # noqa: S110  # cleanup, 锁释放失败可忽略
                pass
            return {
                "status": "skipped",
                "reason": "batch_already_resuming",
                "thread_id": thread_id,
            }

        try:
            # 1.2 其他批次待审批防护（并行恢复 → 重复审批根因修复）
            # 场景：write_todos#1/#2 并行确认触发两个 research_resume_task。
            # 线程 A 恢复时拦截 read_file（Path D 创建 pending 审批）后中断退出并
            # 释放 thread 锁；线程 B（等待 thread 锁后进入）若继续恢复执行，会从
            # 同一 checkpoint 重新拦截同一 read_file（DB 仍 pending → 幂等不命中），
            # 产生重复审批。正确行为：B 检测到"其他批次已有 pending/waiting 审批"
            # 直接退出，等待该审批决断后由新的恢复任务统一恢复。
            # 排除本批次（graph_interrupt_id）：本批次未决断走下方决断检查（自愈/重试）。
            from Django_xm.apps.approvals.models import Approval as _Approval

            _pending_count = _Approval.objects.filter(
                source=_Approval.SOURCE_DEEP_RESEARCH,
                source_id=thread_id,
                state__in=[_Approval.STATE_PENDING, _Approval.STATE_WAITING],
            ).exclude(extra__graph_interrupt_id=graph_interrupt_id).count()
            if _pending_count:
                logger.info(
                    f"[Resume] 其他批次存在 {_pending_count} 条 pending/waiting 审批，"
                    f"退出等待（防并行恢复重复审批）: thread_id={thread_id}, "
                    f"graph_interrupt_id={graph_interrupt_id}"
                )
                return {
                    "status": "skipped",
                    "reason": "pending_approvals_in_other_batches",
                    "thread_id": thread_id,
                }

            # 2. 检查同批次所有审批是否已决断
            all_resume_values, all_resolved = _collect_batch_decisions(
                thread_id,
                graph_interrupt_id,
            )

            if not all_resolved:
                # 2.1 批次自愈：对批次内"已过期且仍为 pending"的审批终态化为 TIMEOUT。
                # 解决 batch_not_all_resolved 永久卡死（用户实测：审批超时后任务停止）：
                # - cleanup_expired_approvals 事务提交竞态（worker 读到 pending）
                # - timeout_approval 后 DB 停留 PROCESSING 未终态化
                # 自愈只终态化（dispatch_resume=False），恢复仍由本任务统一执行。
                from datetime import UTC as _UTC
                from datetime import datetime as _datetime
                from datetime import timedelta as _timedelta

                from django.db.models import Q as _Q

                from Django_xm.apps.approvals.models import Approval as _Approval
                from Django_xm.apps.approvals.services.approval_service import (
                    timeout_approval as _timeout_approval,
                )

                _now = _datetime.now(_UTC)
                _expired_ids = list(
                    _Approval.objects.filter(
                        source=_Approval.SOURCE_DEEP_RESEARCH,
                        source_id=thread_id,
                        extra__graph_interrupt_id=graph_interrupt_id,
                        state=_Approval.STATE_PENDING,
                    )
                    .filter(
                        _Q(expires_at__lt=_now)
                        | _Q(
                            expires_at__isnull=True,
                            created_at__lt=_now - _timedelta(seconds=300),
                        )
                    )
                    .values_list("interrupt_id", flat=True)
                )
                for _interrupt_id in _expired_ids:
                    _timeout_approval(_interrupt_id, dispatch_resume=False)
                if _expired_ids:
                    logger.info(
                        f"[Resume] 批次自愈终态化 {len(_expired_ids)} 条过期审批: "
                        f"thread_id={thread_id}, graph_interrupt_id={graph_interrupt_id}"
                    )
                    all_resume_values, all_resolved = _collect_batch_decisions(
                        thread_id,
                        graph_interrupt_id,
                    )

            if not all_resolved:
                # 2.2 仍有未决断（未过期/真实等待）：延时重试（防竞态），
                # 避免 batch_not_all_resolved 直接退出导致任务永久停住。
                if self.request.retries < self.max_retries:
                    logger.info(
                        f"[Resume] 同批次尚有未决断审批，延时重试: "
                        f"thread_id={thread_id}, graph_interrupt_id={graph_interrupt_id}, "
                        f"resolved={len(all_resume_values)}, retries={self.request.retries}"
                    )
                    raise self.retry(
                        countdown=5,
                        exc=RuntimeError(
                            f"batch_not_all_resolved: thread_id={thread_id}, "
                            f"graph_interrupt_id={graph_interrupt_id}"
                        ),
                    )
                logger.warning(
                    f"[Resume] 同批次尚有未决断审批，重试耗尽退出: "
                    f"thread_id={thread_id}, graph_interrupt_id={graph_interrupt_id}, "
                    f"resolved={len(all_resume_values)}"
                )
                return {
                    "status": "waiting",
                    "reason": "batch_not_all_resolved",
                    "thread_id": thread_id,
                    "resolved_count": len(all_resume_values),
                }

            if not all_resume_values:
                logger.warning(
                    f"[Resume] 同批次无审批决策: thread_id={thread_id}, graph_interrupt_id={graph_interrupt_id}"
                )
                if chat_session_id:
                    _publish_research_failure(thread_id, "同批次无审批决策")
                return {
                    "status": "error",
                    "reason": "no_decisions",
                    "thread_id": thread_id,
                }

            logger.info(f"[Resume] 同批次全部决断，开始恢复: thread_id={thread_id}, decisions={all_resume_values}")

            # 审批终态化：对批次内所有已确认的审批记录调用 complete_approval，
            # 防止 DB 记录残留 pending 污染后续批次计数。
            # all_resume_values = {langgraph_resume_id: {tool_call_id: bool}} 或 {tool_call_id: bool}
            _finalize_batch_approvals(all_resume_values, thread_id)

            # 3. 加载 ResearchTask
            from Django_xm.apps.research.models import ResearchTask

            try:
                task = ResearchTask.objects.get(task_id=thread_id, is_deleted=False)
            except ResearchTask.DoesNotExist:
                logger.exception(f"[Resume] ResearchTask 不存在: {thread_id}")
                if chat_session_id:
                    _publish_research_failure(thread_id, f"ResearchTask 不存在: {thread_id}")
                return {
                    "status": "error",
                    "reason": "task_not_found",
                    "thread_id": thread_id,
                }

            tracker.mark_started()
            tracker.update_progress(10, "审批恢复启动")

            # 4. 构建 Command(resume=...)
            from langgraph.types import Command

            # resume_by_interrupt = {langgraph_resume_id: {tool_call_id: bool}}
            # Command(resume=...) 的 key 必须是 LangGraph Interrupt.id（langgraph_resume_id）
            resume_command: "Command[Any]" = Command(resume=all_resume_values)
            logger.info(f"[Resume] 构建恢复命令: resume_values={all_resume_values}")

            # 5. 在同一事件循环中构建 agent + 执行恢复
            async def _create_and_resume():
                agent = await _build_research_agent_from_task(task)

                result = await execute_research_async(
                    agent,
                    query=None,  # 恢复模式，不发送新 query
                    thread_id=thread_id,
                    disable_llm_cache=True,
                    user_id=user_id,
                    chat_session_id=chat_session_id or task.session_id,
                    message_id=message_id,
                    resume_command=resume_command,
                )

                # 释放异步 Checkpointer 连接
                try:
                    from Django_xm.apps.ai_engine.services.checkpointer_factory import release_async_checkpointer

                    await release_async_checkpointer()
                except Exception:  # noqa: S110  # cleanup, checkpointer 资源释放失败可忽略
                    pass

                return result

            result = asyncio.run(_create_and_resume())
            tracker.update_progress(50, "智能体恢复执行中")

            response_time = round(time.time() - start_time, 2)

            # 6. 处理结果
            # - success=True → finalize_research 发布结果
            # - error="interrupted" → 新的审批已创建，退出等待新恢复任务
            # - 其他错误 → 标记失败
            if result.success:
                logger.info(f"[Resume] 恢复成功: thread_id={thread_id}")
                finalize_research(
                    thread_id,
                    result,
                    response_time,
                    sync_files=True,
                    publish_to_redis=bool(chat_session_id),
                )
                # 回写 ChatMessage + 广播 stream_completed，确保前端感知完成
                if chat_session_id:
                    try:
                        from Django_xm.apps.research.services.writeback import (
                            writeback_to_chat_message,
                            broadcast_stream_completed,
                        )
                        message_id = writeback_to_chat_message(
                            thread_id, result.final_report, success=True,
                            chat_session_id=chat_session_id,
                        )
                        broadcast_stream_completed(
                            chat_session_id, thread_id, success=True,
                            final_report=result.final_report, message_id=message_id,
                            user_id=task.created_by_id,
                        )
                    except Exception as e:
                        logger.warning(f"[Resume] 回写 ChatMessage 失败: {e}")
                tracker.mark_success(
                    result={
                        "thread_id": thread_id,
                        "final_report": result.final_report,
                    }
                )
                cleanup_research_sandbox.apply_async(args=(thread_id,), countdown=120)
                return {"status": "success", "thread_id": thread_id}

            elif result.error_message == "interrupted":
                # Path D 退出：新的审批已创建，worker 退出
                # 等待用户对新 Approval 做决策，触发新的 research_resume_task
                logger.info(f"[Resume] 恢复过程中再次 interrupt，退出等待新审批: thread_id={thread_id}")
                tracker.update_progress(50, "等待新审批")
                update_task_status(thread_id, {"status": "pending"})
                return {
                    "status": "interrupted",
                    "thread_id": thread_id,
                    "message": "恢复过程中产生新的审批请求，等待用户决策",
                }

            else:
                logger.warning(f"[Resume] 恢复失败: thread_id={thread_id}, error={result.error_message}")
                tracker.mark_failure(error_message=result.error_message)
                if chat_session_id:
                    _publish_research_failure(thread_id, result.error_message)
                return {
                    "status": "error",
                    "thread_id": thread_id,
                    "error": result.error_message,
                }

        finally:
            # 释放批次恢复锁 + thread 恢复锁
            try:
                cache.delete(lock_key)
            except Exception:  # noqa: S110  # cleanup, 锁释放失败可忽略
                pass
            try:
                cache.delete(thread_lock_key)
            except Exception:  # noqa: S110  # cleanup, 锁释放失败可忽略
                pass

    except Retry:
        # Celery 重试信号：必须向上抛出，不能当作失败处理
        # （否则会误发布失败结果，且吞掉重试机制）
        raise
    except Exception as exc:
        # 区分可恢复/终态异常：可恢复异常（LLM 连接/超时/限流、checkpoint 错误）
        # 走 Celery 指数退避重试，不发布终态失败（避免前端显示错误终态）；
        # 重试耗尽或终态异常才标记失败并发布失败结果。
        try:
            from Django_xm.apps.ai_engine.services.exceptions import classify_exception

            _classified = classify_exception(exc)
            _recoverable = _classified.recoverable
        except Exception:
            _recoverable = False
        if _recoverable and self.request.retries < self.max_retries:
            logger.warning(
                f"[Resume] 可恢复异常，退避重试: thread_id={thread_id}, "
                f"retry={self.request.retries + 1}/{self.max_retries}, "
                f"error={str(exc)[:200]}"
            )
            raise self.retry(exc=exc)
        logger.exception(
            f"[Resume] 深度研究恢复任务终态失败: thread_id={thread_id}",
        )
        tracker.mark_failure(error_message=str(exc))
        if chat_session_id:
            _publish_research_failure(thread_id, str(exc))
        return {"status": "error", "thread_id": thread_id, "error": str(exc)}
