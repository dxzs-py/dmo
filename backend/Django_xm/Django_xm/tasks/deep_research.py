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

            result = await execute_research_async(agent, query, thread_id, disable_llm_cache=True)

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
            logger.warning(f"[Celery] 研究逻辑失败：{thread_id}, {result.error_message}")
            _mark_failed(result.error_message)
            return {"status": "error", "thread_id": thread_id, "error": result.error_message}

        finalize_research(thread_id, result, response_time, sync_files=True, publish_to_redis=publish_to_redis)

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
        _mark_failed(str(exc), exc)
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


def _collect_batch_decisions(thread_id: str, graph_interrupt_id: str) -> tuple[dict, bool]:
    """收集同批次所有审批决策。

    批量 interrupt 场景：一个 interrupt 包含多个工具审批，
    所有工具审批完成后才能恢复 agent。

    Args:
        thread_id: 研究任务 ID
        graph_interrupt_id: 批次 ID

    Returns:
        (all_resume_values, all_resolved)
        - all_resume_values: {tool_call_id: bool} 决策 dict
        - all_resolved: 是否所有审批都已决断（approved/rejected/timeout）
    """
    from Django_xm.apps.approvals.models import Approval

    batch_approvals = Approval.objects.filter(
        source=Approval.SOURCE_DEEP_RESEARCH,
        source_id=thread_id,
        extra__graph_interrupt_id=graph_interrupt_id,
    )

    all_resume_values = {}
    all_resolved = True

    for approval in batch_approvals:
        extra = approval.extra if isinstance(approval.extra, dict) else {}
        tc_id = extra.get("tool_call_id", approval.interrupt_id)

        if approval.state == Approval.STATE_APPROVED:
            all_resume_values[tc_id] = True
        elif approval.state == Approval.STATE_REJECTED:
            all_resume_values[tc_id] = False
        elif approval.state == Approval.STATE_TIMEOUT:
            all_resume_values[tc_id] = False  # 超时视为拒绝
        else:
            # pending 状态 → 未决断
            all_resolved = False

    return all_resume_values, all_resolved


@shared_task(
    bind=True,
    name="research.resume",
    max_retries=1,
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

        # 1. 并发安全：获取批次恢复锁
        # 同一批次的多个审批可能同时触发 research_resume_task，
        # 只有第一个获取锁的任务执行恢复，其余退出。
        from django.core.cache import cache

        lock_key = f"research:resume:lock:{graph_interrupt_id}"
        # SET NX + EX 300：锁有效期 5 分钟（足够恢复执行）
        lock_acquired = cache.add(lock_key, "1", timeout=300)
        if not lock_acquired:
            logger.info(f"[Resume] 同批次恢复锁已被占用，跳过: graph_interrupt_id={graph_interrupt_id}")
            return {
                "status": "skipped",
                "reason": "batch_already_resuming",
                "thread_id": thread_id,
            }

        try:
            # 2. 检查同批次所有审批是否已决断
            all_resume_values, all_resolved = _collect_batch_decisions(
                thread_id,
                graph_interrupt_id,
            )

            if not all_resolved:
                logger.info(
                    f"[Resume] 同批次尚有未决断审批，退出: "
                    f"thread_id={thread_id}, "
                    f"graph_interrupt_id={graph_interrupt_id}, "
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
                return {
                    "status": "error",
                    "reason": "no_decisions",
                    "thread_id": thread_id,
                }

            logger.info(f"[Resume] 同批次全部决断，开始恢复: thread_id={thread_id}, decisions={all_resume_values}")

            # 3. 加载 ResearchTask
            from Django_xm.apps.research.models import ResearchTask

            try:
                task = ResearchTask.objects.get(task_id=thread_id, is_deleted=False)
            except ResearchTask.DoesNotExist:
                logger.exception(f"[Resume] ResearchTask 不存在: {thread_id}")
                return {
                    "status": "error",
                    "reason": "task_not_found",
                    "thread_id": thread_id,
                }

            tracker.mark_started()
            tracker.update_progress(10, "审批恢复启动")

            # 4. 构建 Command(resume=...)
            from langgraph.types import Command

            # all_resume_values = {tool_call_id: bool, ...}
            # 整个 dict 作为 interrupt() 的返回值传递给 middleware
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
                    publish_to_redis=False,
                )
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
                return {
                    "status": "interrupted",
                    "thread_id": thread_id,
                    "message": "恢复过程中产生新的审批请求，等待用户决策",
                }

            else:
                logger.warning(f"[Resume] 恢复失败: thread_id={thread_id}, error={result.error_message}")
                tracker.mark_failure(error_message=result.error_message)
                return {
                    "status": "error",
                    "thread_id": thread_id,
                    "error": result.error_message,
                }

        finally:
            # 释放批次恢复锁
            try:
                cache.delete(lock_key)
            except Exception:  # noqa: S110  # cleanup, 锁释放失败可忽略
                pass

    except Exception as exc:
        logger.exception(
            f"[Resume] 深度研究恢复任务异常: thread_id={thread_id}",
        )
        tracker.mark_failure(error_message=str(exc))
        return {"status": "error", "thread_id": thread_id, "error": str(exc)}
