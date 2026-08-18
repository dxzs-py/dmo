"""
深度研究公共执行服务（会话级单执行流）

- 智能体执行（LLM Cache 控制 + Token 追踪）
- 结果处理（文件同步 + Token 更新 + 状态更新）
- 审批中断：on_interrupt 挂起等待批次决策，返回非空 decisions dict，
  adapter 自动 Command(resume=...) 重入，单协程持续运行

统一性：
    - 审批创建：与 chat 模块共用 request_approval_async（统一 DB 记录）
    - 审批恢复：与 chat 模块共用 ApprovalResumeView → ApprovalGateway（统一端点）
    - 事件发布：与 chat 模块共用 publish_approval（统一实时同步）
"""

import logging
import os
from dataclasses import dataclass
from typing import Any

from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler

logger = logging.getLogger(__name__)

APPROVAL_TIMEOUT_SECONDS = 300


@dataclass
class ResearchResult:
    success: bool
    final_report: str = ""
    files: dict[str, Any] | None = None
    state_files: dict[str, Any] | None = None
    usage_data: dict[str, int] | None = None
    model_name: str = ""
    error_message: str = ""
    reasoning: str = ""
    # 子代理图层正文/思考累计（Agent 图层嵌套规范 Task 1.5，来自 adapter.subagent_contents）
    subagent_contents: dict[str, dict[str, str]] | None = None
    # 子代理工具条目会话级聚合（tool_call_id → entry，来自 adapter._subagent_tool_entries）：
    # 图层字段贯通（subagent_thread_id/agent_name/depth + seq/position），
    # 由 writeback 合并进 ChatMessage.tool_calls（乱序根源修复，与 chat 链路同构）
    subagent_tool_entries: dict[str, dict] | None = None
    # 主 agent 累计正文（来自 adapter._main_content）：过程信息展示权威源，
    # 由 writeback 落库到 ChatMessage.content / ResearchTask.content。
    main_content: str = ""
    raw_result: dict[str, Any] | None = None
    # 业务等待挂起（spec D4，非审批）：父 Graph 等待子代理结果，退出协程待调度器唤醒。
    # 支持批量（fan-out/fan-in）：一次等待多个子代理，全部终态后一次性恢复。
    suspended: bool = False
    subagent_thread_ids: list[str] = None
    interrupt_id: str = ""


def load_research_context(task_id: str, max_content_length: int = 12000) -> str:
    """加载指定研究任务的上下文（final_report + 关键文件内容）

    续研时注入到 system_prompt，提供先前研究的结构化摘要。
    """
    from Django_xm.apps.core.services.file_manager import get_file_manager
    from Django_xm.apps.research.models import ResearchTask

    try:
        task = ResearchTask.objects.filter(task_id=task_id, is_deleted=False).first()
        if not task:
            return ""

        parts = []

        if task.final_report and task.final_report.strip():
            report = task.final_report.strip()
            if len(report) > max_content_length:
                report = report[:max_content_length] + "\n...(报告过长已截断)"
            parts.append(f"### 研究报告\n{report}")

        try:
            file_manager = get_file_manager()
            files = file_manager.list_task_files(task_id, "research")
            md_files = [f for f in files if f.path.suffix in (".md", ".txt")]
            for f in md_files[:8]:
                relative_path = str(f.path.relative_to(f.base_dir))
                content = file_manager.read_file_content(task_id, relative_path, "research")
                if content and content.strip():
                    truncated = content.strip()
                    if len(truncated) > 3000:
                        truncated = truncated[:3000] + "\n...(内容过长已截断)"
                    parts.append(f"### {relative_path}\n{truncated}")
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning(f"加载研究文件失败: {e}")

        return "\n\n".join(parts)

    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(f"加载研究上下文失败: {e}")
        return ""


async def execute_research_async(
    agent,
    query: str,
    thread_id: str,
    disable_llm_cache: bool = True,
    *,
    user_id: int | None = None,
    chat_session_id: str | None = None,
    message_id: str = "",
    resume_command=None,
    interrupt_handler=None,
) -> ResearchResult:
    """异步研究执行逻辑（会话级单执行流，执行器模式）。

    使用 agent.astream_research_with_interrupts() 执行，当工具需要用户确认时：
    - interrupt_handler（执行器模式必传）：创建审批 + 挂起等待批次决策，
      返回非空 decisions dict，astream 自动 Command(resume=decisions) 重入，
      单协程持续运行，无需外部恢复任务。

    Args:
        agent: 已创建的研究智能体
        query: 研究查询（resume_mode 时为 None）
        thread_id: 线程 ID（= research task_id，用于 checkpoint 寻址）
        disable_llm_cache: 是否禁用 LLM Cache
        user_id: 任务归属用户 ID（审批创建时写入 approval.user）
        chat_session_id: 关联 chat 会话 ID（跨模块同步事件路由）
        message_id: 关联 chat message ID（前端消息定位）
        resume_command: 恢复模式时传入 Command(resume=...)，None 表示初始执行
        interrupt_handler: 审批中断异步回调（interrupts_data -> decisions dict），
            返回空 dict 表示审批创建失败，由 adapter 抛错终止

    Returns:
        ResearchResult 标准化结果
    """
    saved_cache = None
    if disable_llm_cache:
        from langchain_core.globals import get_llm_cache, set_llm_cache

        saved_cache = get_llm_cache()
        set_llm_cache(None)

    try:
        with TokenUsageCallbackHandler() as cb:
            result = await agent.astream_research_with_interrupts(
                query,
                config={
                    "configurable": {
                        "chat_session_id": chat_session_id,
                        "assistant_message_id": message_id,
                    }
                },
                callbacks=[cb],
                on_interrupt=interrupt_handler,
                resume_command=resume_command,
            )

        usage_data = {
            "prompt_tokens": cb.prompt_tokens,
            "completion_tokens": cb.completion_tokens,
            "successful_requests": cb.successful_requests,
        }
        model_name = getattr(cb, "_current_model", "") or ""

        success = result.get("success", True)
        final_report = result.get("final_report", "")
        error_message = result.get("error", "") if not success else ""
        suspended = bool(result.get("suspended", False))

        return ResearchResult(
            success=success,
            final_report=final_report,
            files=result.get("files"),
            state_files=result.get("state_files"),
            usage_data=usage_data,
            model_name=model_name,
            error_message=error_message,
            reasoning=getattr(agent, "accumulated_reasoning", ""),
            subagent_contents=result.get("subagent_contents"),
            subagent_tool_entries=result.get("subagent_tool_entries"),
            main_content=result.get("main_content") or "",
            raw_result=result,
            suspended=suspended,
            subagent_thread_ids=result.get("subagent_thread_ids", []) or [],
            interrupt_id=result.get("interrupt_id", ""),
        )
    finally:
        if disable_llm_cache and saved_cache is not None:
            from langchain_core.globals import set_llm_cache

            set_llm_cache(saved_cache)


def _normalize_file_path(path: str) -> str:
    path = path.lstrip("/")
    if path.startswith(("notes/", "reports/", "plans/")):
        return path
    name = path.split("/")[-1]
    name_lower = name.lower()
    if "report" in name_lower:
        return f"reports/{name}"
    if "plan" in name_lower:
        return f"plans/{name}"
    if name.endswith((".md", ".txt")):
        return f"notes/{name}"
    return path


def _sync_state_files_to_disk(thread_id: str, result: ResearchResult):
    try:
        from Django_xm.apps.core.services.file_manager import get_file_manager

        files = result.state_files or result.files or {}
        if not files:
            logger.info(f"无状态文件需要同步: {thread_id}")
            return

        fm = get_file_manager()
        synced = 0
        for file_path, file_data in files.items():
            path = _normalize_file_path(file_path)
            content = file_data
            if isinstance(file_data, dict):
                content = file_data.get("content", "")
                if isinstance(content, list):
                    content = "\n".join(content)
            if isinstance(content, str) and content:
                fm.write_file_content(thread_id, path, content, task_type="research")
                synced += 1

        logger.info(f"状态文件同步完成: {thread_id}, {synced}/{len(files)} 个文件")
    except Exception as e:
        logger.warning(f"同步状态文件到磁盘失败: {e}")


def finalize_research(
    thread_id: str,
    result: ResearchResult,
    response_time: float,
    sync_files: bool = True,
) -> None:
    """
    公共结果处理逻辑

    Args:
        thread_id: 研究任务 ID
        result: ResearchResult 执行结果
        response_time: 响应时间（秒）
        sync_files: 是否同步文件到磁盘
    """
    if sync_files:
        _sync_state_files_to_disk(thread_id, result)

    try:
        from Django_xm.apps.research.services.cross_app import update_research_task_model_and_tokens

        total_tokens = 0
        token_detail = None
        if result.usage_data:
            total_tokens = result.usage_data.get("prompt_tokens", 0) + result.usage_data.get("completion_tokens", 0)

        update_research_task_model_and_tokens(
            task_id=thread_id,
            model_name=result.model_name,
            token_count=total_tokens,
            token_detail=token_detail,
            response_time=response_time,
        )
    except Exception as e:
        logger.warning(f"更新研究任务 Token 数据失败: {e}")

    # 成功时立即更新 DB 状态为 completed
    # 确保 SSE 流读取到的状态是 completed，避免前端显示 progress/running 后收不到 completed 事件
    if result.success:
        try:
            from Django_xm.apps.research.services.task_manager import update_task_status as _update_status

            _update_status(
                thread_id,
                {
                    "status": "completed",
                    "current_step": "completed",
                    "final_report": result.final_report,
                    "content": result.main_content or "",
                },
            )
        except Exception as e:
            logger.warning(f"更新研究任务完成状态失败: {e}")

    if result.usage_data:
        logger.info(
            f"研究任务 Token 统计: {thread_id}, "
            f"模型: {result.model_name}, "
            f"Token: {result.usage_data.get('prompt_tokens', 0) + result.usage_data.get('completion_tokens', 0)} "
            f"(输入={result.usage_data.get('prompt_tokens', 0)}, "
            f"输出={result.usage_data.get('completion_tokens', 0)}), "
            f"调用: {result.usage_data.get('successful_requests', 0)}次"
        )


# ---------------------------------------------------------------------------
# 审批批次决策与执行器恢复辅助
# （collect_batch_decisions / finalize_batch_approvals 已收敛到
#   common.approval_batch，仅保留 self_heal_expired_approvals 研究专属）
# ---------------------------------------------------------------------------


def self_heal_expired_approvals(thread_id: str, graph_interrupt_id: str) -> int:
    """批次自愈：对批次内已过期且仍为 pending 的审批终态化为 TIMEOUT。

    Args:
        thread_id: 研究任务 ID
        graph_interrupt_id: 批次 ID

    Returns:
        int: 自愈终态化数量
    """
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
    return len(_expired_ids)


def cleanup_research_sandbox(thread_id: str) -> dict:
    """研究完成后删除 sandbox 目录（含 skill 工具等临时资源）。

    注意：sandbox 仅含临时工具文件（如 skill 脚本），不含用户可见的研究产出；
    研究产出（notes/plans/reports）存储在 research/{thread_id}/ 根目录，由守卫保护。

    Args:
        thread_id: 研究任务 ID

    Returns:
        dict: 清理结果状态
    """
    import shutil

    from django.conf import settings as django_settings

    data_dir = str(
        getattr(django_settings, "DATA_DIR", None)
        or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data"
        )
    )
    sandbox_dir = os.path.join(data_dir, "research", thread_id, "sandbox")

    if not os.path.isdir(sandbox_dir):
        return {"status": "skipped", "reason": "sandbox not found"}

    shutil.rmtree(sandbox_dir, ignore_errors=True)
    logger.info(f"[Research] 已清理 sandbox 目录: {sandbox_dir}")
    return {"status": "success", "cleaned": sandbox_dir}
