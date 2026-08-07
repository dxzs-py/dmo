"""
深度研究公共执行服务

提取 Celery 任务和聊天路径的公共逻辑：
- 智能体执行（LLM Cache 控制 + Token 追踪）
- 结果处理（文件同步 + Token 更新 + 状态更新 + Redis 发布）
- 异步执行 + interrupt 审批机制（Path D：DB 持久化 + Celery 恢复）

Path D 架构变更：
    旧实现：_handle_interrupt 阻塞等待 Redis pubsub 响应（worker 卡死 5min）
    新实现：_handle_interrupt_create_and_exit 创建 Approval DB 记录 + 发布事件 + 返回空 dict（退出信号）
           worker 退出后，用户审批时由 research_resume_task Celery 任务从 checkpoint 恢复

统一性：
    - 审批创建：与 chat 模块共用 request_approval_async（统一 DB 记录）
    - 审批恢复：与 chat 模块共用 ApprovalResumeView → ApprovalGateway（统一端点）
    - 事件发布：与 chat 模块共用 publish_approval（统一实时同步）
"""

import json
import logging
from dataclasses import dataclass
from typing import Any

from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler

logger = logging.getLogger(__name__)

REDIS_CHANNEL_PREFIX = "research:result:"
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
    raw_result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "final_report": self.final_report,
            "files": self.files,
            "state_files": self.state_files,
            "usage_data": self.usage_data,
            "model_name": self.model_name,
            "error_message": self.error_message,
        }


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


def execute_research(
    agent,
    query: str,
    disable_llm_cache: bool = True,
) -> ResearchResult:
    """
    公共研究执行逻辑

    Args:
        agent: 已创建的研究智能体
        query: 研究查询
        disable_llm_cache: 是否禁用 LLM Cache（避免缓存干扰研究结果）

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
            result = agent.research(query, callbacks=[cb])

        usage_data = {
            "prompt_tokens": cb.prompt_tokens,
            "completion_tokens": cb.completion_tokens,
            "successful_requests": cb.successful_requests,
        }
        model_name = getattr(cb, "_current_model", "") or ""

        success = result.get("success", True)
        final_report = result.get("final_report", "")
        error_message = result.get("error", "") if not success else ""

        return ResearchResult(
            success=success,
            final_report=final_report,
            files=result.get("files"),
            state_files=result.get("state_files"),
            usage_data=usage_data,
            model_name=model_name,
            error_message=error_message,
            raw_result=result,
        )
    finally:
        if disable_llm_cache and saved_cache is not None:
            from langchain_core.globals import set_llm_cache

            set_llm_cache(saved_cache)


async def _handle_interrupt_create_and_exit(
    interrupts_data,
    *,
    thread_id: str,
    user_id: int | None = None,
    chat_session_id: str | None = None,
    message_id: str = "",
    data: dict[str, Any] | None = None,
) -> dict:
    """审批中断回调：创建 Approval DB 记录 + 发布事件 + 返回退出信号（Path D）。

    不阻塞等待决策。worker 创建审批后返回空 dict（退出信号），
    astream_research_with_interrupts 检测到空返回值后退出循环，
    Celery 任务结束（worker 释放）。

    用户决策后由新 Celery 任务（research_resume_task）从 checkpoint 恢复 agent 执行。

    与 chat 模块统一：
    - 共用 request_approval_async 创建 Approval DB 记录（统一持久化）
    - 共用 publish_approval 发布实时事件（统一事件通道）
    - 共用 ApprovalResumeView 接收用户决策（统一端点）

    Args:
        interrupts_data: 单个 interrupt dict 或 interrupt dict list
        thread_id: 研究任务 ID（= approval.source_id）
        user_id: 任务归属用户 ID（用于 approval.user 外键）
        chat_session_id: 关联的 chat 会话 ID（用于跨模块同步事件路由）
        message_id: 关联的 chat message ID（前端用于精确定位消息）

    Returns:
        dict: 空字典表示"退出"（不恢复 agent），astream_research_with_interrupts 检测到后退出循环
    """
    # 兼容：单个 dict 自动包装为 list
    if isinstance(interrupts_data, dict):
        interrupts_data = [interrupts_data]

    if not interrupts_data:
        return {}

    logger.info(
        f"[ResearchApproval] 收到 {len(interrupts_data)} 个审批请求: "
        f"tools={[i.get('tool_name', 'unknown') for i in interrupts_data]}, "
        f"task_id={thread_id}"
    )

    from Django_xm.apps.approvals.models import Approval
    from Django_xm.apps.approvals.services.approval_service import request_approval_async

    # 提取批次 ID（graph_interrupt_id）和 langgraph_resume_id
    # 同一批次的审批共享 graph_interrupt_id，下游统一从 _meta 读取
    first_interrupt = interrupts_data[0] if interrupts_data else {}
    graph_interrupt_id = first_interrupt.get("graph_interrupt_id", "") or ""
    # langgraph_resume_id = LangGraph Interrupt.id，作为 Command(resume=...) 的 KEY
    langgraph_resume_id = first_interrupt.get("langgraph_resume_id", "") or ""

    # 为每个 interrupt 创建 Approval DB 记录
    for interrupt_data in interrupts_data:
        interrupt_id = interrupt_data.get("interrupt_id", "")
        if not interrupt_id:
            logger.warning(f"[ResearchApproval] 跳过缺少 interrupt_id 的审批请求: {interrupt_data}")
            continue

        tool_name = interrupt_data.get("tool_name", "unknown")
        tool_call_id = interrupt_data.get("tool_call_id", "") or interrupt_id

        # 构建 base_extra（中断解析时已携带的字段：risk_level / 嵌套层级等）
        from Django_xm.apps.approvals.services.approval_service import build_approval_extra

        base_extra: dict[str, Any] = {}
        risk_level = interrupt_data.get("risk_level")
        if risk_level:
            base_extra["risk_level"] = risk_level
        for field in ("parent_tool_call_id", "depth", "agent_name", "agent_path"):
            val = interrupt_data.get(field)
            if val is not None and val not in ("", []):
                base_extra[field] = val

        # approval_data 与 chat 模块字段对齐（统一 schema）
        approval_data = {
            "tool_name": tool_name,
            "title": interrupt_data.get("title", "确认操作"),
            "description": interrupt_data.get("description", ""),
            "operation": interrupt_data.get("operation", ""),
            "danger_level": interrupt_data.get("danger_level", "medium"),
            "parameters": interrupt_data.get("parameters", {}) or interrupt_data.get("args", {}) or {},
            "action": interrupt_data.get("action", Approval.ACTION_CONFIRM),
            "session_id": chat_session_id,
            "message_id": message_id,
            "extra": build_approval_extra(
                data or {},
                tool_call_id=tool_call_id,
                graph_interrupt_id=graph_interrupt_id,
                langgraph_resume_id=langgraph_resume_id,
                message_id=message_id,
                base_extra=base_extra,
            ),
        }

        try:
            await request_approval_async(
                source=Approval.SOURCE_DEEP_RESEARCH,
                source_id=thread_id,
                interrupt_id=interrupt_id,
                approval_data=approval_data,
            )
            logger.info(
                f"[ResearchApproval] 已创建审批 DB 记录: "
                f"interrupt_id={interrupt_id}, tool={tool_name}, "
                f"task_id={thread_id}, risk_level={risk_level or 'controlled'}"
            )
        except Exception:
            logger.exception(
                f"[ResearchApproval] 创建审批 DB 记录失败: "
                f"interrupt_id={interrupt_id}, tool={tool_name}, "
                f"task_id={thread_id}",
            )
            # 创建失败不影响其他审批请求的创建，但当前请求会被跳过
            # agent 不会收到 resume_value，interrupt 会保留在 checkpoint 中
            # research_resume_task 在用户审批时会从 checkpoint 恢复

    # 返回空 dict 表示"退出"（不恢复 agent）
    # astream_research_with_interrupts 检测到空返回值后返回 interrupted 结果
    logger.info(
        f"[ResearchApproval] 已创建 {len(interrupts_data)} 个审批记录，worker 退出: "
        f"task_id={thread_id}, graph_interrupt_id={graph_interrupt_id}"
    )
    return {}


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
    data: dict[str, Any] | None = None,
) -> ResearchResult:
    """异步研究执行逻辑（Path D：DB 持久化 + Celery 恢复）

    使用 agent.astream_research_with_interrupts() 执行，
    当工具需要用户确认时：
    1. on_interrupt 回调创建 Approval DB 记录 + 发布事件
    2. on_interrupt 返回空 dict（退出信号）
    3. astream_research_with_interrupts 检测到空返回值后退出，返回 interrupted 结果
    4. Celery 任务结束（worker 释放）
    5. 用户审批后由 research_resume_task 从 checkpoint 恢复

    Args:
        agent: 已创建的研究智能体
        query: 研究查询（resume_mode 时为 None）
        thread_id: 线程 ID（= research task_id，用于 checkpoint 寻址）
        disable_llm_cache: 是否禁用 LLM Cache
        user_id: 任务归属用户 ID（审批创建时写入 approval.user）
        chat_session_id: 关联 chat 会话 ID（跨模块同步事件路由）
        message_id: 关联 chat message ID（前端消息定位）
        resume_command: 恢复模式时传入 Command(resume=...)，None 表示初始执行

    Returns:
        ResearchResult 标准化结果（interrupted 时 success=False, error_message='interrupted'）
    """
    saved_cache = None
    if disable_llm_cache:
        from langchain_core.globals import get_llm_cache, set_llm_cache

        saved_cache = get_llm_cache()
        set_llm_cache(None)

    try:
        with TokenUsageCallbackHandler() as cb:
            # 审批中断回调：创建 DB 记录 + 退出（Path D）
            async def _on_interrupt(interrupts_data):
                return await _handle_interrupt_create_and_exit(
                    interrupts_data,
                    thread_id=thread_id,
                    user_id=user_id,
                    chat_session_id=chat_session_id,
                    message_id=message_id,
                    data=data,
                )

            result = await agent.astream_research_with_interrupts(
                query,
                config={
                    "configurable": {
                        "chat_session_id": chat_session_id,
                        "assistant_message_id": message_id,
                    }
                },
                callbacks=[cb],
                on_interrupt=_on_interrupt,
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

        return ResearchResult(
            success=success,
            final_report=final_report,
            files=result.get("files"),
            state_files=result.get("state_files"),
            usage_data=usage_data,
            model_name=model_name,
            error_message=error_message,
            raw_result=result,
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


def publish_result_payload(thread_id: str, payload: dict) -> None:
    """发布研究结果负载到 Redis channel（与聊天 SSE 订阅方同一数据库）。

    聊天模块深度研究模式的 SSE 生成器（deep_chat_service.py 的
    _wait_for_research_result_streaming）使用 settings.CELERY_BROKER_URL
    （Redis DB 3）订阅 research:result:{thread_id} 通道。
    Redis Pub/Sub 按数据库隔离，因此发布方必须使用同一 broker URL
    （同一 Redis DB），否则订阅方永远收不到消息。发布频率低，单次连接即可。

    Args:
        thread_id: 研究任务 ID（channel 后缀）
        payload: 结果负载（业务字段，将连同 thread_id 一并序列化发布）
    """
    from django.conf import settings as django_settings

    broker_url = getattr(django_settings, "CELERY_BROKER_URL", "")
    if not broker_url:
        logger.warning("CELERY_BROKER_URL 未配置，无法发布研究结果")
        return

    try:
        import redis as redis_lib

        channel = f"{REDIS_CHANNEL_PREFIX}{thread_id}"
        redis_client = redis_lib.Redis.from_url(broker_url)
        redis_client.publish(
            channel,
            json.dumps({"thread_id": thread_id, **payload}, ensure_ascii=False),
        )
        logger.info(f"研究结果已发布到 Redis: {channel}")
    except Exception as e:
        logger.warning(f"发布研究结果到 Redis 失败: {e}")


def _publish_result_to_redis(thread_id: str, result: ResearchResult, response_time: float):
    """委托 publish_result_payload 发布成功研究结果（保持调用点不变）。"""
    publish_result_payload(thread_id, {"response_time": response_time, **result.to_dict()})


def finalize_research(
    thread_id: str,
    result: ResearchResult,
    response_time: float,
    sync_files: bool = True,
    publish_to_redis: bool = False,
) -> None:
    """
    公共结果处理逻辑

    Args:
        thread_id: 研究任务 ID
        result: ResearchResult 执行结果
        response_time: 响应时间（秒）
        sync_files: 是否同步文件到磁盘
        publish_to_redis: 是否将结果发布到 Redis（供聊天路径订阅）
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

    # 成功时立即更新 DB 状态为 completed（在 publish_to_redis 之前）
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

    if publish_to_redis:
        _publish_result_to_redis(thread_id, result, response_time)
