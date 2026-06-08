"""
深度研究 Celery 任务
将原本的 threading 后台任务迁移到 Celery 任务队列
"""
import asyncio
import logging
import os
import shutil
import time
from celery import shared_task
from celery.exceptions import Retry

from Django_xm.apps.agent_hub import create as agent_hub_create, AgentType, AgentConfig
from Django_xm.apps.research.services.official_deep_agent import OfficialDeepAgentAdapter
from Django_xm.apps.research.services.research_runner import execute_research, finalize_research
from Django_xm.apps.research.services.task_manager import update_task_status
from Django_xm.apps.knowledge.services.multi_kb_retriever import build_retriever_tool_for_research
from Django_xm.tasks.base import TrackedTask

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name='research.run_research',
    max_retries=2,
    default_retry_delay=60,
    soft_time_limit=1800,
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    retry_backoff=True,
    retry_backoff_max=60,
)
def run_research_task(self, thread_id: str, query: str,
                      enable_web_search: bool = True,
                      enable_doc_analysis: bool = False,
                      knowledge_base_ids: list = None,
                      user_id: int = None,
                      use_mcp: bool = False,
                      selected_mcp_servers: list = None,
                      selected_tools: list = None,
                      provider_id: str = None,
                      model_name: str = None,
                      enable_deep_thinking: bool = False,
                      temperature: float = None,
                      max_tokens: int = None,
                      special_params: dict = None,
                      continue_task_id: str = None,
                      publish_to_redis: bool = False):
    tracker = TrackedTask(self)
    if user_id:
        tracker.set_created_by(user_id)
    tracker.set_task_type('deep_research')
    tracker.set_task_manager_id(thread_id, sync_fn=update_task_status)

    start_time = time.time()

    def _mark_failed(error_msg: str, exc: Exception = None):
        tracker.mark_failure(error_message=error_msg)
        if exc and self.request.retries < self.max_retries:
            raise self.retry(exc=exc)

    try:
        logger.info(f"[Celery] 深度研究任务开始：{thread_id}")
        tracker.mark_started()
        tracker.update_progress(10, '研究任务启动')

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
            except Exception as e:
                logger.error(f"[Celery] 构建知识库检索工具异常: {e}")

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
                    extra_tools = asyncio.run(registry.build_tools_for_agent_async(
                        "deep_research", capabilities, tool_config=tool_config,
                    ))
                    if extra_tools:
                        logger.info(f"[Celery] 通过 CapabilityRegistry 加载 {len(extra_tools)} 个额外工具")
                else:
                    from Django_xm.apps.tools import get_tools_for_request_async
                    extra_tools = asyncio.run(get_tools_for_request_async(
                        use_tools=True,
                        use_web_search=False,
                        use_mcp=use_mcp,
                        selected_mcp_servers=selected_mcp_servers,
                        selected_tools=selected_tools,
                        user_id=user_id,
                        tool_tier="extended",
                    ))
                    if extra_tools:
                        logger.info(f"[Celery] 回退直接加载 {len(extra_tools)} 个额外工具")
            except Exception as e:
                logger.warning(f"[Celery] 加载额外工具失败: {e}")

        selected_skill_names = None
        if selected_tools:
            selected_skill_names = [
                name.replace("skill_", "", 1)
                for name in selected_tools
                if name.startswith("skill_")
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
                data_dir = str(getattr(django_settings, "DATA_DIR", None) or os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    "data"
                ))
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

        agent = asyncio.run(agent_hub_create(config))

        from Django_xm.apps.agent_hub.factory import AgentWrapper
        if isinstance(agent, AgentWrapper):
            agent = OfficialDeepAgentAdapter(graph=agent.graph, thread_id=thread_id, work_dir=agent.work_dir)
        tracker.update_progress(30, '智能体初始化完成')

        result = execute_research(agent, query, disable_llm_cache=True)

        response_time = round(time.time() - start_time, 2)
        tracker.update_progress(90, '研究执行完成')

        if not result.success:
            logger.warning(f"[Celery] 研究逻辑失败：{thread_id}, {result.error_message}")
            _mark_failed(result.error_message)
            return {'status': 'error', 'thread_id': thread_id, 'error': result.error_message}

        finalize_research(thread_id, result, response_time, sync_files=True, publish_to_redis=publish_to_redis)

        cleanup_research_sandbox.apply_async(args=[thread_id], countdown=120)

        tracker.mark_success(result={
            'thread_id': thread_id,
            'final_report': result.final_report,
        })
        logger.info(f"[Celery] 深度研究任务完成：{thread_id}")
        return {'status': 'success', 'thread_id': thread_id}

    except Retry:
        raise
    except Exception as exc:
        logger.error(f"[Celery] 深度研究任务失败：{thread_id}, 错误：{exc}", exc_info=True)
        _mark_failed(str(exc), exc)
        cleanup_research_sandbox.apply_async(args=[thread_id], countdown=120)
        return {'status': 'error', 'thread_id': thread_id, 'error': str(exc)}


@shared_task(
    name='research.cleanup_sandbox',
    max_retries=1,
    soft_time_limit=60,
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    retry_backoff=True,
    retry_backoff_max=60,
)
def cleanup_research_sandbox(thread_id: str):
    try:
        from django.conf import settings as django_settings
        data_dir = str(getattr(django_settings, "DATA_DIR", None) or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "data"
        ))
        sandbox_dir = os.path.join(data_dir, "research", thread_id, "sandbox")
        work_dir = os.path.join(data_dir, "research", thread_id)

        if not os.path.isdir(sandbox_dir):
            return {'status': 'skipped', 'reason': 'sandbox not found'}

        moved = 0
        for root, dirs, fnames in os.walk(sandbox_dir):
            for fname in fnames:
                if not fname.endswith('.md'):
                    continue
                src = os.path.join(root, fname)
                rel = os.path.relpath(src, sandbox_dir).replace("\\", "/")
                if rel.startswith("skills/") or rel.startswith("mcp/") or rel.startswith("deps/") or rel.startswith("tmp/") or rel.startswith("inherited/"):
                    continue
                if "notes" in rel.lower():
                    target_subdir = "notes"
                elif "plan" in rel.lower():
                    target_subdir = "plans"
                elif "report" in rel.lower():
                    target_subdir = "reports"
                else:
                    target_subdir = "notes"
                target_dir = os.path.join(work_dir, target_subdir)
                os.makedirs(target_dir, exist_ok=True)
                dst = os.path.join(target_dir, fname)
                if os.path.exists(dst):
                    os.remove(dst)
                shutil.move(src, dst)
                moved += 1
                logger.info(f"[Celery] 安全兜底：移动 {rel} -> {target_subdir}/{fname}")

        shutil.rmtree(sandbox_dir, ignore_errors=True)
        logger.info(f"[Celery] 已清理 sandbox 目录: {sandbox_dir}, 移动 {moved} 个残留文件")
        return {'status': 'success', 'cleaned': sandbox_dir, 'moved': moved}
    except Exception as e:
        logger.error(f"[Celery] 清理 sandbox 失败: {e}")
        return {'status': 'error', 'error': str(e)}
