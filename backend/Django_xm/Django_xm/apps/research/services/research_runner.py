"""
深度研究公共执行服务

提取 Celery 任务和聊天路径的公共逻辑：
- 智能体执行（LLM Cache 控制 + Token 追踪）
- 结果处理（文件同步 + Token 更新 + 状态更新 + Redis 发布）
- 异步执行 + interrupt 审批机制（Redis 通信）
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler

logger = logging.getLogger(__name__)

REDIS_CHANNEL_PREFIX = "research:result:"
REDIS_APPROVAL_PREFIX = "research:approval:"
REDIS_APPROVAL_RESPONSE_PREFIX = "research:approval:response:"
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
            files = file_manager.list_task_files(task_id, 'research')
            md_files = [f for f in files if f.path.suffix in ('.md', '.txt')]
            for f in md_files[:8]:
                relative_path = str(f.path.relative_to(f.base_dir))
                content = file_manager.read_file_content(task_id, relative_path, 'research')
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
            'prompt_tokens': cb.prompt_tokens,
            'completion_tokens': cb.completion_tokens,
            'successful_requests': cb.successful_requests,
        }
        model_name = getattr(cb, '_current_model', '') or ''

        success = result.get('success', True)
        final_report = result.get('final_report', '')
        error_message = result.get('error', '') if not success else ''

        return ResearchResult(
            success=success,
            final_report=final_report,
            files=result.get('files'),
            state_files=result.get('state_files'),
            usage_data=usage_data,
            model_name=model_name,
            error_message=error_message,
            raw_result=result,
        )
    finally:
        if disable_llm_cache and saved_cache is not None:
            from langchain_core.globals import set_llm_cache
            set_llm_cache(saved_cache)


async def execute_research_async(
    agent,
    query: str,
    thread_id: str,
    disable_llm_cache: bool = True,
) -> ResearchResult:
    """异步研究执行逻辑（支持 interrupt 审批机制）

    使用 agent.astream_research_with_interrupts() 执行，
    当工具需要用户确认时，通过 Redis 在 Celery worker 和前端之间传递审批请求和响应。

    Args:
        agent: 已创建的研究智能体
        query: 研究查询
        thread_id: 线程 ID（用于 Redis 频道标识）
        disable_llm_cache: 是否禁用 LLM Cache

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

            async def _handle_interrupt(interrupts_data):
                """审批中断回调：通过 Redis 通知前端并等待所有响应（async）

                支持批量处理：当 LLM 一次返回多个 tool_call 导致多个 interrupt 时，
                全部发布给前端，等待用户逐一审批后，返回 {interrupt_id: resume_value} dict。

                关键设计（实时同步统一性）：
                - 全部 Redis 阻塞调用通过 ``asyncio.to_thread`` 卸载到线程池，
                  避免阻塞事件循环，确保审批等待期间其他 SSE 流的心跳与
                  跨浏览器同步事件正常处理。
                - ``pubsub.get_message(timeout=1.0)`` 是主阻塞点（每次最多 1s），
                  必须在线程中执行，否则会冻结事件循环导致所有 SSE 客户端断连。

                Args:
                    interrupts_data: 单个 interrupt dict（兼容旧调用）或 interrupt dict list

                Returns:
                    dict: {interrupt_id: resume_value}，用于 Command(resume=...)
                """
                # 兼容：单个 dict 自动包装为 list
                if isinstance(interrupts_data, dict):
                    interrupts_data = [interrupts_data]

                if not interrupts_data:
                    return {}

                logger.info(
                    f"[ResearchApproval] 收到 {len(interrupts_data)} 个审批请求: "
                    f"tools={[i.get('tool_name', 'unknown') for i in interrupts_data]}"
                )

                try:
                    from django.core.cache import cache
                    redis_client = await asyncio.to_thread(cache.client.get_client)
                except Exception as e:
                    logger.error(f"[ResearchApproval] 获取 Redis 客户端失败: {e}")
                    return {i["interrupt_id"]: False for i in interrupts_data}

                approval_channel = f"{REDIS_APPROVAL_PREFIX}{thread_id}"
                response_channel = f"{REDIS_APPROVAL_RESPONSE_PREFIX}{thread_id}"

                # 1. 批量发布所有审批请求到 Redis
                pending_ids = set()
                id_to_tool = {}  # interrupt_id -> tool_name 映射，超时/已处理通知需要
                approval_list_key = f"{REDIS_APPROVAL_PREFIX}pending:{thread_id}"
                for interrupt_data in interrupts_data:
                    interrupt_id = interrupt_data.get("interrupt_id", "")
                    if not interrupt_id:
                        continue
                    pending_ids.add(interrupt_id)
                    id_to_tool[interrupt_id] = interrupt_data.get("tool_name", "unknown")
                    approval_payload = json.dumps({
                        "type": "approval",
                        "tool_name": interrupt_data.get("tool_name", "unknown"),
                        "interrupt_id": interrupt_id,
                        "title": interrupt_data.get("title", "确认操作"),
                        "description": interrupt_data.get("description", ""),
                        "operation": interrupt_data.get("operation", ""),
                        "danger_level": interrupt_data.get("danger_level", "medium"),
                        "action": interrupt_data.get("action", "confirm"),
                        "state": "pending",
                        "source": "deep_research",
                        "task_id": thread_id,
                    }, ensure_ascii=False)
                    # 透传 parameters（工具输入参数，前端展示用，非空时才显示输入区）
                    if interrupt_data.get("parameters"):
                        approval_payload_obj = json.loads(approval_payload)
                        approval_payload_obj["parameters"] = interrupt_data["parameters"]
                        approval_payload = json.dumps(approval_payload_obj, ensure_ascii=False)
                    try:
                        await asyncio.to_thread(redis_client.publish, approval_channel, approval_payload)
                        # 缓存到 Redis List，供后续订阅者（如 DeepResearchView SSE 流）读取历史审批
                        await asyncio.to_thread(redis_client.rpush, approval_list_key, approval_payload)
                        await asyncio.to_thread(redis_client.expire, approval_list_key, 3600)  # 1小时，覆盖审批等待+页面刷新场景
                    except Exception as e:
                        logger.error(f"[ResearchApproval] 发布审批请求失败(id={interrupt_id}): {e}")

                logger.info(f"[ResearchApproval] 已发布 {len(pending_ids)} 个审批请求: {approval_channel}")

                if not pending_ids:
                    return {}

                # 2. 订阅审批响应频道，等待所有审批响应
                resume_dict = {}
                timed_out_ids = set()
                pubsub = await asyncio.to_thread(redis_client.pubsub)
                try:
                    await asyncio.to_thread(pubsub.subscribe, response_channel)
                    logger.info(f"[ResearchApproval] 等待 {len(pending_ids)} 个审批响应: {response_channel}")

                    deadline = time.time() + APPROVAL_TIMEOUT_SECONDS
                    while pending_ids and time.time() < deadline:
                        # 关键：pubsub.get_message 是阻塞调用（最多 1s），
                        # 必须在线程中执行，否则冻结事件循环导致其他 SSE 流断连
                        message = await asyncio.to_thread(pubsub.get_message, timeout=1.0)
                        if message and message["type"] == "message":
                            try:
                                response_data = json.loads(message["data"])
                                resp_interrupt_id = response_data.get("interrupt_id", "")

                                # 忽略不属于当前批次的响应
                                if resp_interrupt_id not in pending_ids:
                                    logger.debug(
                                        f"[ResearchApproval] 忽略不匹配的响应: "
                                        f"expected_one_of={pending_ids}, got={resp_interrupt_id}"
                                    )
                                    continue

                                approved = response_data.get("approved", False)
                                user_input = response_data.get("user_input")
                                if approved:
                                    resume_dict[resp_interrupt_id] = user_input if user_input is not None else True
                                    logger.info(
                                        f"[ResearchApproval] 审批通过: id={resp_interrupt_id}"
                                    )
                                else:
                                    resume_dict[resp_interrupt_id] = False
                                    logger.info(
                                        f"[ResearchApproval] 审批拒绝: id={resp_interrupt_id}"
                                    )
                                pending_ids.discard(resp_interrupt_id)

                                # 从 Redis List 中移除已处理的审批
                                try:
                                    pending_list = await asyncio.to_thread(
                                        redis_client.lrange, approval_list_key, 0, -1
                                    )
                                    for item in pending_list:
                                        try:
                                            item_data = json.loads(item)
                                            if item_data.get("interrupt_id") == resp_interrupt_id:
                                                await asyncio.to_thread(
                                                    redis_client.lrem, approval_list_key, 1, item
                                                )
                                                break
                                        except (json.JSONDecodeError, KeyError):
                                            continue
                                except Exception:
                                    pass

                                # 写入已处理标记（含完整审批数据），供 SSE 历史补偿推送已处理审批的最终状态
                                try:
                                    processed_key = f"{REDIS_APPROVAL_PREFIX}processed:{thread_id}:{resp_interrupt_id}"
                                    processed_data = json.dumps({
                                        "interrupt_id": resp_interrupt_id,
                                        "tool_name": id_to_tool.get(resp_interrupt_id, "unknown"),
                                        "approved": approved,
                                        "state": "approved" if approved else "rejected",
                                        "source": "deep_research",
                                        "task_id": thread_id,
                                    }, ensure_ascii=False)
                                    await asyncio.to_thread(
                                        redis_client.setex, processed_key, 3600, processed_data
                                    )
                                except Exception:
                                    pass

                                # 发布"审批已处理"通知到审批频道，让双端 SSE 流同步更新 UI
                                try:
                                    processed_payload = json.dumps({
                                        "type": "approval_processed",
                                        "interrupt_id": resp_interrupt_id,
                                        "tool_name": id_to_tool.get(resp_interrupt_id, "unknown"),
                                        "approved": approved,
                                        "state": "approved" if approved else "rejected",
                                        "source": "deep_research",
                                        "task_id": thread_id,
                                    }, ensure_ascii=False)
                                    await asyncio.to_thread(
                                        redis_client.publish, approval_channel, processed_payload
                                    )
                                except Exception:
                                    pass

                            except (json.JSONDecodeError, KeyError) as e:
                                logger.warning(f"[ResearchApproval] 解析审批响应失败: {e}")
                                continue

                    # 3. 超时处理：未响应的 interrupt 视为拒绝
                    if pending_ids:
                        logger.warning(
                            f"[ResearchApproval] {len(pending_ids)} 个审批超时({APPROVAL_TIMEOUT_SECONDS}s): "
                            f"ids={pending_ids}, 视为拒绝"
                        )
                        for tid in pending_ids:
                            resume_dict[tid] = False
                            timed_out_ids.add(tid)

                        # 发布超时通知到审批频道，让前端更新审批状态
                        for tid in timed_out_ids:
                            try:
                                timeout_payload = json.dumps({
                                    "type": "approval_timeout",
                                    "interrupt_id": tid,
                                    "tool_name": id_to_tool.get(tid, "unknown"),
                                    "source": "deep_research",
                                    "state": "timeout",
                                    "task_id": thread_id,
                                }, ensure_ascii=False)
                                await asyncio.to_thread(
                                    redis_client.publish, approval_channel, timeout_payload
                                )
                                # 写入超时标记（含完整数据），供 SSE 历史补偿推送
                                processed_key = f"{REDIS_APPROVAL_PREFIX}processed:{thread_id}:{tid}"
                                timeout_data = json.dumps({
                                    "interrupt_id": tid,
                                    "tool_name": id_to_tool.get(tid, "unknown"),
                                    "approved": False,
                                    "state": "timeout",
                                    "source": "deep_research",
                                    "task_id": thread_id,
                                }, ensure_ascii=False)
                                await asyncio.to_thread(
                                    redis_client.setex, processed_key, 3600, timeout_data
                                )
                            except Exception as pub_err:
                                logger.warning(f"[ResearchApproval] 发布超时通知失败: {pub_err}")

                finally:
                    try:
                        await asyncio.to_thread(pubsub.unsubscribe, response_channel)
                        await asyncio.to_thread(pubsub.close)
                    except Exception:
                        pass

                return resume_dict

            result = await agent.astream_research_with_interrupts(
                query, callbacks=[cb], on_interrupt=_handle_interrupt,
            )

        usage_data = {
            'prompt_tokens': cb.prompt_tokens,
            'completion_tokens': cb.completion_tokens,
            'successful_requests': cb.successful_requests,
        }
        model_name = getattr(cb, '_current_model', '') or ''

        success = result.get('success', True)
        final_report = result.get('final_report', '')
        error_message = result.get('error', '') if not success else ''

        return ResearchResult(
            success=success,
            final_report=final_report,
            files=result.get('files'),
            state_files=result.get('state_files'),
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
    path = path.lstrip('/')
    if path.startswith(('notes/', 'reports/', 'plans/')):
        return path
    name = path.split('/')[-1]
    name_lower = name.lower()
    if 'report' in name_lower:
        return f'reports/{name}'
    if 'plan' in name_lower:
        return f'plans/{name}'
    if name.endswith(('.md', '.txt')):
        return f'notes/{name}'
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
                content = file_data.get('content', '')
                if isinstance(content, list):
                    content = '\n'.join(content)
            if isinstance(content, str) and content:
                fm.write_file_content(thread_id, path, content, task_type='research')
                synced += 1

        logger.info(f"状态文件同步完成: {thread_id}, {synced}/{len(files)} 个文件")
    except Exception as e:
        logger.warning(f"同步状态文件到磁盘失败: {e}")


def _publish_result_to_redis(thread_id: str, result: ResearchResult, response_time: float):
    try:
        from django.core.cache import cache
        redis_client = cache.client.get_client()
        channel = f"{REDIS_CHANNEL_PREFIX}{thread_id}"
        payload = json.dumps({
            "thread_id": thread_id,
            "response_time": response_time,
            **result.to_dict(),
        }, ensure_ascii=False)
        redis_client.publish(channel, payload)
        logger.info(f"研究结果已发布到 Redis: {channel}")
    except Exception as e:
        logger.warning(f"发布研究结果到 Redis 失败: {e}")


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
            total_tokens = result.usage_data.get('prompt_tokens', 0) + result.usage_data.get('completion_tokens', 0)

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
            _update_status(thread_id, {
                'status': 'completed',
                'current_step': 'completed',
                'final_report': result.final_report,
            })
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
