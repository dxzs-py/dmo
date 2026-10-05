"""深研 Agent 物料装配（E7/审计 §3.2-1：自 SessionExecutor 下移 research 服务层）。

职责边界：把"构建一个深研 Agent 所需的物料装配"从会话执行器（session_executor）
中解耦——本模块只负责"造 agent"，不持有任何会话状态：
- 知识库检索工具构建（build_retriever_tool_for_research）；
- MCP / 用户自选工具三分类加载（capabilities registry → tools 服务降级）；
- Skills 包目录加载（SkillAdapter，同步 ORM 须经 sync_to_async 调用）；
- 续研上下文文案模板与父任务文件继承（inherit_parent_files）；
- 工具去重合并、AgentConfig 组装 + 异步 Checkpointer 注入 + agent_hub 构建。

调用方（SessionExecutor._build_agent 薄委托）传入 params/thread_id/user_id，
拿回可直接执行的 agent 实例。

注：续研 system_prompt 基底（DEEP_RESEARCH_SYSTEM_PROMPT）自权威定义处
apps/research/prompts.py 直接导入（E8 第 4 消费点正规化：不再借道 deep_builder
re-export，deep_builder 侧同名常量与本模块持有同一对象，行为等价）。
"""

import logging

from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)


def load_skill_dirs(selected_skill_names: list[str], *, user_id: int | None = None) -> list[str]:
    """加载选中 Skill 包的目录列表（同步 ORM，须经 sync_to_async 调用）。

    Args:
        selected_skill_names: 已剥离 skill_ 前缀的 Skill 包名列表
        user_id: 所属用户（SkillAdapter 属主过滤依据）

    Returns:
        存在且有效的 skill_dir 列表（供 DeepAgent skills 参数）
    """
    from Django_xm.apps.tools.skills.adapter import SkillAdapter

    adapter = SkillAdapter(user_id=user_id)
    return adapter.to_deep_agent_skills(selected_skill_names=selected_skill_names)


async def inherit_parent_files(continue_task_id: str, thread_id: str) -> None:
    """续研模式：继承父任务文件到 sandbox 目录（供 agent 读取）。

    原 SessionExecutor._inherit_parent_files 静态方法函数化下移，行为等价。
    """
    import os
    import shutil

    try:
        from django.conf import settings as django_settings

        data_dir = str(
            getattr(django_settings, "DATA_DIR", None)
            or os.path.join(
                # 与原 session_executor 锚点等价：原文件（services/fastapi_service/）
                # 上三级为 Django_xm 包根；本文件（apps/research/services/）上四级同点
                os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                ),
                "data",
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
            logger.info(
                f"[SessionExecutor] 已继承父任务文件到 sandbox: "
                f"{parent_work_dir} -> {inherited_dir}"
            )
    except Exception as e:
        logger.warning(f"[SessionExecutor] 继承父任务文件失败: {e}")


async def build_research_agent(params: dict, *, thread_id: str, user_id: int | None = None):
    """构建研究智能体（原 SessionExecutor._build_agent 主体；初始执行：从 start 信令参数构建）。

    缓存（model/embedding registry、SystemConfig）由 ai_engine AppConfig.ready()
    在进程启动时同步预热，此处不重复预热（异步上下文无法访问 ORM）。

    Args:
        params: start 信令参数（工具开关 / 模型配置 / continue_task_id 等）
        thread_id: 研究任务线程 id（= ResearchTask.task_id，作为 agent session_id）
        user_id: 所属用户

    Returns:
        经 agent_hub 构建的可执行 agent 实例（AgentConfig 为中间组装产物）
    """
    from Django_xm.apps.agent_hub import AgentConfig, AgentType
    from Django_xm.apps.agent_hub import create as agent_hub_create

    # 1. 知识库检索工具
    retriever_tool = None
    if params.get("enable_doc_analysis") and params.get("knowledge_base_ids") and user_id:
        try:
            from Django_xm.apps.knowledge.services.multi_kb_retriever import build_retriever_tool_for_research

            retriever_tool = await sync_to_async(build_retriever_tool_for_research)(
                knowledge_base_ids=params.get("knowledge_base_ids"),
                user_id=user_id,
            )
        except Exception:
            logger.exception("[SessionExecutor] 构建知识库检索工具异常")

    # 2. 额外工具（MCP / 自定义工具）
    extra_tools = []
    if params.get("use_mcp") or params.get("selected_tools"):
        try:
            from Django_xm.apps.ai_engine.capabilities import registry

            capabilities = registry.get_default_capabilities("deep_research")
            if "tool_injection" in capabilities:
                tool_config = {
                    "use_tools": True,
                    "use_web_search": False,
                    "use_mcp": params.get("use_mcp"),
                    "selected_tools": params.get("selected_tools"),
                    "selected_mcp_servers": params.get("selected_mcp_servers"),
                    "user_id": user_id,
                    "tool_tier": "extended",
                }
                extra_tools = await registry.build_tools_for_agent_async(
                    "deep_research", capabilities, tool_config=tool_config
                )
            else:
                from Django_xm.apps.tools import get_tools_for_request_async

                extra_tools = await get_tools_for_request_async(
                    use_tools=True,
                    use_web_search=False,
                    use_mcp=params.get("use_mcp"),
                    selected_mcp_servers=params.get("selected_mcp_servers"),
                    selected_tools=params.get("selected_tools"),
                    user_id=user_id,
                    tool_tier="extended",
                )
            if extra_tools:
                logger.info(f"[SessionExecutor] 加载 {len(extra_tools)} 个额外工具")
        except Exception as e:
            logger.warning(f"[SessionExecutor] 加载额外工具失败: {e}")

    # 3. Skills
    skills = None
    selected_skill_names = [
        name.replace("skill_", "", 1)
        for name in (params.get("selected_tools") or [])
        if name.startswith("skill_")
    ]
    if selected_skill_names:
        try:
            # SkillAdapter 内部为同步 ORM 查询，必须经 sync_to_async 进入线程池
            # （直调会抛 SynchronousOnlyOperation 导致 Skill 静默不加载）
            skill_dirs = await sync_to_async(
                load_skill_dirs, thread_sensitive=True
            )(selected_skill_names, user_id=user_id)
            if skill_dirs:
                skills = skill_dirs
        except Exception as e:
            logger.warning(f"[SessionExecutor] 加载 Skill 目录失败: {e}")

    # 4. 续研上下文（continue_task_id）
    research_context = ""
    continue_task_id = params.get("continue_task_id")
    if continue_task_id:
        from Django_xm.apps.research.services.research_runner import load_research_context

        ctx = await sync_to_async(load_research_context)(continue_task_id)
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
        await inherit_parent_files(continue_task_id, thread_id)

    system_prompt = None
    if research_context:
        # E8 第 4 消费点正规化：常量权威定义在 apps/research/prompts.py（单一来源），
        # 直接从定义处导入，不再借道 deep_builder 的 re-export（原为审计 §1.2-4
        # 隐藏消费点：deep_builder 改为函数内延迟导入该常量时此处会静默炸 ImportError）
        from Django_xm.apps.research.prompts import DEEP_RESEARCH_SYSTEM_PROMPT

        system_prompt = f"{DEEP_RESEARCH_SYSTEM_PROMPT}\n\n---\n\n{research_context}"

    # 5. 组装工具列表与 AgentConfig
    # 三分类组合加载（与 chat 代理模式 ToolService.get_tools 对齐）：
    #   系统自带（standard 层，visibility=core）+ 按开关（网络搜索，
    #   visibility=switch）+ 用户选择（selected_tools，visibility=selectable，
    #   由 extra_tools 加载）+ 文档检索（retriever）。
    # 三类 visibility 天然不重合；config.tools 必须传「完整组合」，否则
    # resolve_tools 的显式工具集分支（config.tools 非空即直接返回）会丢失
    # 系统自带工具——用户选择了任一工具后主 agent 只剩用户选的 + spawn/wait。
    from Django_xm.apps.tools import get_tools_for_request_async as _get_tools_for_request

    tools = await _get_tools_for_request(
        use_tools=True,
        use_web_search=params.get("enable_web_search", True),
        use_mcp=False,  # MCP 工具由 extra_tools（用户选择类）单独加载
        user_id=user_id,
        tool_tier="standard",
    )
    _names = {getattr(t, "name", "") for t in tools}
    if retriever_tool and getattr(retriever_tool, "name", "") not in _names:
        tools.append(retriever_tool)
        _names.add(getattr(retriever_tool, "name", ""))
    if extra_tools:
        for _t in extra_tools:
            _n = getattr(_t, "name", "")
            if _n and _n not in _names:
                tools.append(_t)
                _names.add(_n)

    tool_config = {
        "use_tools": True,
        "use_web_search": params.get("enable_web_search", True),
        "use_doc_analysis": params.get("enable_doc_analysis", False),
        "user_id": user_id,
    }
    config = AgentConfig(
        agent_type=AgentType.DEEP_RESEARCH,
        provider_id=params.get("provider_id"),
        model_name=params.get("model_name"),
        temperature=params.get("temperature"),
        max_tokens=params.get("max_tokens"),
        special_params=params.get("special_params"),
        enable_deep_thinking=params.get("enable_deep_thinking", False),
        tools=tools if tools else None,
        tool_config=tool_config,
        system_prompt=system_prompt,
        user_id=user_id,
        session_id=thread_id,
        skills=skills,
        debug=False,
    )

    # 6. 注入异步 Checkpointer
    from Django_xm.apps.ai_engine.services.checkpointer_factory import get_async_checkpointer

    async_cp = await get_async_checkpointer()
    if async_cp is not None:
        config.checkpointer = async_cp
        logger.info("[SessionExecutor] 使用异步 Checkpointer")

    agent = await agent_hub_create(config)

    return agent
