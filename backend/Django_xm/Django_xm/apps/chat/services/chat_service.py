"""
聊天服务层

将复杂的业务逻辑从视图中分离，提高代码可维护性和可测试性。
深度研究模式委托给 deep_chat_service；RAGChatService 提供知识库检索器
（agent/deep-research 模式的工具注入）。非流式 process_chat_request 编排链
与 rag 分流（process_rag_request）为生产零调用的孤儿链，已随 CT-01=A 删除
（知识库问答由 RAG 独立页面承载）。

子服务拆分：
- AgentService：Agent 创建、配置和生命周期管理
- ChatMessageBuilder：消息构建、转换和内容处理
- ContextService：上下文压缩、注入检测和预算管理
- ToolService：工具获取、过滤和协调

阶段 5 域 mixin 拆分（行为等价纯移动，本类经 MRO 组合承载）：
- ChatModeOrchestrationMixin（chat_mode_mixin）：slash/deep-research/agent 模式分派与事件编排
- AgentStreamMixin（chat_stream_mixin）：agent 流式执行阶段编排 + _StreamExecutionPlan
- SubagentStreamMixin（subagent_stream_mixin）：子代理正文/工具事件回调域 + _SubagentStreamState

拆分边界由测试 patch 面约束定形（详见 .trae/memory/2026-10-06.md 阶段 5 节）：
- 类级 patch（mock.patch.object(ChatService, M)）经 MRO 命中 mixin 方法，
  patch 在本类上设置属性即 shadow，锚定零破坏；
- **方法体引用 chat_service 模块 globals 的方法留在本文件**（名字解析在定义
  模块，patch 打在 chat_service 命名空间才生效）：
  _inject_agent_retriever_tools / _inject_deep_research_retriever_tools
  （sync_to_async，test_retriever_injection_degradation:56/:139）、
  _build_stream_agent_and_input（convert_chat_history，
  test_context_engineering_off_loop:196）。
"""

import asyncio
import logging
import time
from typing import Any

from asgiref.sync import sync_to_async
from langchain_core.messages import HumanMessage

from Django_xm.apps.ai_engine.config import AGENT_RECURSION_LIMIT_CHAT
from Django_xm.apps.chat.models import ChatMode

from ..utils import convert_chat_history
from .agent_service import AgentService
from .chat_message_builder import ChatMessageBuilder
from .chat_mode_mixin import ChatModeOrchestrationMixin
from .chat_stream_mixin import AgentStreamMixin
from .context_service import ContextService, build_context_degradation_events
from .deep_chat_service import DeepChatService
from .rag_chat_service import RAGChatService
from .stream_helpers import build_context_info
from .subagent_stream_mixin import SubagentStreamMixin, _SubagentStreamState  # noqa: F401
from .tool_service import ToolService

logger = logging.getLogger(__name__)


class ChatService(ChatModeOrchestrationMixin, AgentStreamMixin, SubagentStreamMixin):
    """聊天服务类 - 处理聊天相关的业务逻辑

    职责：业务流程编排和协调，具体实现委托给子服务与域 mixin：
    - AgentService: Agent 创建和管理
    - ChatMessageBuilder: 消息构建和转换
    - ContextService: 上下文工程
    - ToolService: 工具管理
    - ChatModeOrchestrationMixin: 模式分派（slash/deep-research/agent）
    - AgentStreamMixin: agent 流式执行阶段编排
    - SubagentStreamMixin: 子代理正文/工具事件回调
    """

    CHECKPOINTER_ENABLED = True

    def __init__(self, user_id: int | None = None, thread_id: str | None = None):
        self.user_id = user_id

        self._agent_service = AgentService(user_id)
        self._message_builder = ChatMessageBuilder(user_id)
        self._context_service = ContextService(user_id, thread_id=thread_id)
        self._tool_service = ToolService(user_id)

        self._rag_service = RAGChatService(user_id=user_id, chat_service=self)
        self._deep_service = DeepChatService(self)

    async def _load_user_knowledge_bases(self) -> list[dict]:
        """加载当前用户的知识库列表（list_knowledge_bases 以 User 实例过滤归属）"""
        from django.contrib.auth import get_user_model

        from Django_xm.apps.knowledge.services.kb_service import list_knowledge_bases

        user = await sync_to_async(get_user_model().objects.get)(pk=self._rag_service.user_id)
        return await sync_to_async(list_knowledge_bases)(user)

    async def _update_last_message_tokens(
        self,
        session_id: str,
        token_count: int,
        token_detail: dict,
        model: str,
        response_time: float,
    ):
        await ChatMessageBuilder.update_last_message_tokens(
            self.user_id,
            session_id,
            token_count,
            token_detail,
            model,
            response_time,
        )

    async def _create_agent_with_memory(
        self,
        data: dict[str, Any],
        prompt_mode: str = "agent",
        tool_config: dict[str, Any] | None = None,
        tools: list | None = None,
    ) -> tuple:
        return await self._agent_service.create_agent_with_memory(
            data,
            prompt_mode,
            tool_config=tool_config,
            tools=tools,
        )

    @staticmethod
    def _resolve_kb_ids(data: dict[str, Any]) -> list[str] | None:
        if not data.get("use_knowledge_base"):
            return None
        kb_ids = list(data.get("selected_knowledge_bases") or [])
        single_kb = data.get("selected_knowledge_base")
        if single_kb and single_kb not in kb_ids:
            kb_ids.append(single_kb)
        return kb_ids if kb_ids else None

    async def _get_tools(self, data: dict[str, Any]) -> list:
        return await self._tool_service.get_tools(data)

    def _build_tool_config(self, data: dict[str, Any]) -> dict[str, Any]:
        from Django_xm.apps.tools import TOOL_TIER_STANDARD

        use_web_search = data.get("use_web_search", False)
        use_mcp = data.get("use_mcp", False)
        selected_tools = data.get("selected_tools")
        selected_mcp_servers = data.get("selected_mcp_servers")
        use_knowledge_base = data.get("use_knowledge_base", False)
        # 尊重前端显式传递的 use_tools 参数
        explicit_use_tools = data.get("use_tools")
        if explicit_use_tools is not None:
            has_any_tool_enabled = bool(explicit_use_tools)
        else:
            has_any_tool_enabled = bool(use_web_search or use_mcp or use_knowledge_base or selected_tools)
        return {
            "use_tools": has_any_tool_enabled,
            "use_web_search": use_web_search,
            "use_mcp": use_mcp,
            "selected_tools": selected_tools,
            "selected_mcp_servers": selected_mcp_servers,
            "user_id": self.user_id,
            "tool_tier": data.get("tool_tier", TOOL_TIER_STANDARD),
        }

    def _build_user_content(self, data: dict[str, Any]) -> dict[str, Any]:
        return self._message_builder.build_user_content(data)

    async def acreate_human_message(self, data: dict[str, Any]) -> HumanMessage:
        """构建用户消息（**公共 API**：供 stream/fallback.py 等协作对象调用）。"""
        return await self._message_builder.acreate_human_message(data)

    def _load_research_context(
        self, research_task_id: str, user_id: int | None = None, session_id: str | None = None
    ) -> str | None:
        return self._context_service.load_research_context(research_task_id, user_id, session_id)

    def apply_context_engineering(
        self,
        chat_history: list[dict[str, Any]],
        query: str,
        mode: str = ChatMode.AGENT,
        model_name: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """应用上下文工程（**公共 API**：供 stream/fallback.py 等协作对象调用）。

        注意：checkpointer 专用变体仍在 `_apply_context_engineering_for_checkpointer`
        （仅类内使用，保持私有）。
        """
        return self._context_service.apply_context_engineering(
            chat_history,
            query,
            mode,
            model_name,
        )

    async def aapply_context_engineering(
        self,
        chat_history: list[dict[str, Any]],
        query: str,
        mode: str = ChatMode.AGENT,
        model_name: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """apply_context_engineering 的异步调度变体（D1/P2-7）。

        同步实现内部含 KG/store 检索与逐条同步 embed_query（远程 API 调用），
        直接在事件循环上执行会阻塞 FastAPI 执行服务（同循环其他会话/WS 广播停摆）。
        经 asyncio.to_thread 移入工作线程执行：仅改调度不改编排，
        输入输出与同步版本完全一致。同步链路（regex/ORM/同步 store/同步 embedding）
        无事件循环依赖，线程安全。
        """
        return await asyncio.to_thread(
            self.apply_context_engineering, chat_history, query, mode, model_name
        )

    def _apply_context_engineering_for_checkpointer(
        self,
        user_message: str,
        model_name: str | None = None,
        mode: str = ChatMode.AGENT,
    ) -> dict[str, Any]:
        return self._context_service.apply_context_engineering_for_checkpointer(
            user_message,
            model_name,
            mode,
        )

    async def run_agent_session(
        self,
        data: dict[str, Any],
        interrupt_handler,
        broadcast,
        should_stop=None,
    ) -> None:
        """chat agent 单协程执行（FastAPI 执行服务调用，执行与连接解耦）。

        与旧 SSE 流式入口同构，但事件经 broadcast 广播到 WS
        （不 yield 到 SSE），审批中断经 interrupt_handler 挂起等待信令。

        Args:
            data: chat 请求数据（含 session_id / _assistant_message_id / message 等）
            interrupt_handler: 审批中断挂起回调，
                async fn(graph_interrupt_id, langgraph_resume_id) -> decisions dict
            broadcast: async fn(event: dict) -> None，将事件广播到 WS
            should_stop: 可选安全点回调（Task 9 优雅停止），
                async 事件循环每次迭代前检查，返回 True 时终止执行。
                由执行服务（SessionExecutor._stop_requested）注入。
        """
        from Django_xm.apps.ai_engine.services.cost_tracker import create_token_detail_tracker
        from Django_xm.apps.ai_engine.services.usage_tracker import create_usage_tracker

        model_name = data.get("model_name")
        from Django_xm.apps.ai_engine.config import settings as ai_settings

        tracker_model_id = model_name or ai_settings.openai_model
        usage_tracker = create_usage_tracker(model_id=tracker_model_id)
        token_detail_tracker = create_token_detail_tracker()
        stream_start_time = time.time()

        session_id = data.get("session_id", "N/A")
        logger.info(
            f"[ChatExec] 开始执行: mode={data.get('mode', 'agent')}, "
            f"session={session_id}, msg={data.get('message', '')[:80]}..."
        )

        await broadcast({"type": "start", "message": "开始生成..."})

        async for event in self._dispatch_by_mode(
            data,
            usage_tracker,
            token_detail_tracker,
            interrupt_handler=interrupt_handler,
        ):
            # 安全点（Task 9 优雅停止）：迭代边界检查停止标志，
            # 终止执行并保留已输出内容与 checkpoint（由 finally 落库广播）。
            if should_stop is not None and should_stop():
                raise asyncio.CancelledError("用户停止生成")
            await broadcast(event)

        # 业务等待挂起（wait_for_subagent，spec D4）：会话未结束，仅暂停等待
        # 子代理终态。不广播 context/end、不更新 tokens（由唤醒恢复后的最终
        # 结束统一处理），返回挂起信息供执行器注册父 awaiter。
        wait_suspend = data.get("_subagent_wait_suspend")
        if wait_suspend:
            logger.info(
                f"[ChatExec] 业务等待挂起返回: session={session_id}, "
                f"subagents={wait_suspend.get('subagent_thread_ids')}, "
                f"interrupt_id={wait_suspend.get('interrupt_id')}"
            )
            return dict(wait_suspend)

        context_info = build_context_info(usage_tracker, token_detail_tracker, stream_start_time)
        await broadcast({"type": "context", "data": context_info})
        await broadcast({"type": "end", "message": "生成完成"})
        usage_tracker.log_summary()
        token_detail_tracker.log_summary()

        session_id_for_update = data.get("session_id")
        if session_id_for_update is not None:
            await self._update_last_message_tokens(
                session_id=session_id_for_update,
                token_count=usage_tracker.get_total_tokens(),
                token_detail=token_detail_tracker.get_token_detail(),
                model=usage_tracker.model_id,
                response_time=round(time.time() - stream_start_time, 2),
            )
        logger.info("[ChatExec] 执行完成")

    async def _inject_deep_research_retriever_tools(self, data: dict[str, Any]) -> None:
        """deep-research 知识库检索工具注入：为每个选中知识库创建 comprehensive 检索工具

        全部工具追加到 _retriever_tool_list，首个作为主检索器 _retriever_tool，
        其余由 _handle_deep_research_mode 合并进 _deep_extra_tools。

        方法体留本文件（阶段 5）：引用 chat_service 模块 globals 的
        ``sync_to_async``（test_retriever_injection_degradation:56/:139 patch
        打在本模块命名空间，名字解析在定义模块，迁出即失效）。
        """
        kb_ids = data.get("selected_knowledge_bases") or []
        single_kb = data.get("selected_knowledge_base")
        if single_kb and single_kb not in kb_ids:
            kb_ids.append(single_kb)

        if not kb_ids:
            return

        from Django_xm.apps.knowledge.services.retriever_tool import create_retriever_tool

        kb_info_map = {}
        try:
            kbs = await self._load_user_knowledge_bases()
            kb_info_map = {kb.get("id"): kb for kb in kbs}
        except Exception:
            # 知识库列表读取失败时回退到使用 kb_id 作为名称
            logger.debug("读取知识库列表失败，使用 kb_id 作为名称")

        for kb_id in kb_ids:
            retriever = await sync_to_async(self._rag_service.get_rag_retriever)(
                kb_id, retrieval_mode="comprehensive"
            )
            if retriever:
                kb_info = kb_info_map.get(kb_id)
                kb_name = kb_id
                kb_desc = ""
                if kb_info:
                    kb_name = kb_info.get("name", kb_id)
                    kb_desc = kb_info.get("description", "")

                tool_name = f"knowledge_base_{kb_id}".replace("-", "_").replace(" ", "_")
                tool_desc = f"搜索知识库「{kb_name}」中的相关信息。"
                if kb_desc:
                    tool_desc += f" 知识库描述: {kb_desc}"

                retriever_tool = create_retriever_tool(
                    retriever,
                    name=tool_name,
                    description=tool_desc,
                    retrieval_mode="comprehensive",
                    kb_name=kb_name,
                    kb_description=kb_desc,
                )
                data.setdefault("_retriever_tool_list", []).append(retriever_tool)
            else:
                # E5-02：注入失败不得静默——用户以为答案来自知识库，实际 LLM 凭
                # 参数知识回答。追加降级事件由执行编排广播（用户可见提示）。
                data.setdefault("_stream_degradation_events", []).append(
                    {
                        "kb_id": kb_id,
                        "message": f"知识库「{kb_id}」暂时无法检索，本次回答将不使用该知识库内容",
                    }
                )
                logger.warning(f"[ChatExec] deep-research 检索器注入失败（已发降级提示）: kb={kb_id}")

        if data.get("_retriever_tool_list"):
            data["_retriever_tool"] = data["_retriever_tool_list"][0]

    async def _inject_agent_retriever_tools(self, data: dict[str, Any]) -> None:
        """agent 模式知识库检索工具注入：为每个选中知识库创建 auto 检索工具（precise 主检索器 + comprehensive 备用）

        方法体留本文件（阶段 5）：引用 chat_service 模块 globals 的
        ``sync_to_async``（test_retriever_injection_degradation:56/:139 patch
        打在本模块命名空间，名字解析在定义模块，迁出即失效）。
        """
        kb_ids = data.get("selected_knowledge_bases") or []
        single_kb = data.get("selected_knowledge_base")
        if single_kb and single_kb not in kb_ids:
            kb_ids.append(single_kb)

        for kb_id in kb_ids:
            retriever = await sync_to_async(self._rag_service.get_rag_retriever)(
                kb_id, retrieval_mode="precise"
            )
            if retriever:
                try:
                    comprehensive_retriever = await sync_to_async(self._rag_service.get_rag_retriever)(
                        kb_id, retrieval_mode="comprehensive"
                    )
                except Exception as e:
                    logger.warning(f"创建 comprehensive 检索器失败: {e}")
                    comprehensive_retriever = None

                from Django_xm.apps.ai_engine.services.llm_factory import get_helper_model
                from Django_xm.apps.knowledge.services.retriever_tool import create_retriever_tool

                kb_info = None
                try:
                    kbs = await self._load_user_knowledge_bases()
                    kb_info = next((kb for kb in kbs if kb.get("id") == kb_id), None)
                except Exception:
                    # 知识库信息读取失败时回退到使用 kb_id 作为名称
                    logger.debug("读取知识库 %s 信息失败", kb_id)

                kb_name = kb_id
                kb_desc = ""
                if kb_info:
                    kb_name = kb_info.get("name", kb_id)
                    kb_desc = kb_info.get("description", "")

                tool_name = f"knowledge_base_{kb_id}".replace("-", "_").replace(" ", "_")
                tool_desc = f"搜索知识库「{kb_name}」中的相关信息。"
                if kb_desc:
                    tool_desc += f" 知识库描述: {kb_desc}"

                retriever_tool = create_retriever_tool(
                    retriever,
                    name=tool_name,
                    description=tool_desc,
                    retrieval_mode="auto",
                    comprehensive_retriever=comprehensive_retriever,
                    llm=get_helper_model(),
                    kb_name=kb_name,
                    kb_description=kb_desc,
                )
                data.setdefault("_extra_tools", []).append(retriever_tool)
            else:
                # E5-02：注入失败不得静默（同 deep-research 分支，用户可见降级提示）
                data.setdefault("_stream_degradation_events", []).append(
                    {
                        "kb_id": kb_id,
                        "message": f"知识库「{kb_id}」暂时无法检索，本次回答将不使用该知识库内容",
                    }
                )
                logger.warning(f"[ChatExec] agent 检索器注入失败（已发降级提示）: kb={kb_id}")

    async def _build_stream_agent_and_input(
        self,
        data: dict[str, Any],
        tool_config: dict[str, Any],
        tools: list,
        research_context,
    ) -> tuple[Any, Any, Any, Any]:
        """Agent 与 graph_input/config 构造（Checkpointer/非 Checkpointer 双分支）。

        返回 (agent, graph_input, config, agent_config)；agent_config.model 为
        AgentFactory 统一创建的模型包装实例（带 fallback），供调用方复用。

        方法体留本文件（阶段 5）：引用 chat_service 模块 globals 的
        ``convert_chat_history``（test_context_engineering_off_loop:196 patch
        打在本模块命名空间，名字解析在定义模块，迁出即失效）。
        """
        # 1.2 运行时动态注入：checkpointer 主链路的记忆注入文本在 agent 构造前获取。
        # get_injection_context 为同步方法（含 KG 检索与逐条同步 embed_query 远程调用），
        # 经 asyncio.to_thread 移入工作线程（与 aapply_context_engineering 同调度策略）；
        # 失败降级为不注入（warning 留痕），不阻断对话。判定条件与
        # AgentService.create_agent_with_memory 的 use_checkpointer 同源
        # （CHECKPOINTER_ENABLED and session_id）。注入文本经 data 传递（主 agent 与
        # 预创建降级 agent 同源接线），由 MemoryInjectionMiddleware 在每次模型调用前
        # request.override 合并进最后一条 user message（注入不进
        # graph_input/checkpoint state，且位于尾部易变区不破坏缓存前缀）。
        # 文档上下文/当前时间不经本通道：由 builder 链易变分区
        # （_common.build_dynamic_context_prompt → attach_volatile_injection）
        # 经 merge_injection_text 并入同一注入块（去重），避免双注入。
        memory_injection_context = ""
        if AgentService.CHECKPOINTER_ENABLED and data.get("session_id"):
            try:
                memory_injection_context = await asyncio.to_thread(
                    self._context_service.get_injection_context,
                    data.get("message", ""),
                    data.get("session_id"),
                )
            except Exception as e:
                logger.warning(f"[ChatExec] 记忆注入获取失败，降级为不注入: {e}")
            if memory_injection_context:
                memory_injection_context = (
                    f"{memory_injection_context.strip()}\n\n"
                    "（以上为系统注入的参考信息（历史会话、知识图谱记忆、当前时间、"
                    "已上传文档摘要等），供回答时参考；与当前问题无关的内容可忽略。）"
                )
        data["_memory_injection_context"] = memory_injection_context

        agent, thread_config, use_checkpointer, agent_config = await self._create_agent_with_memory(
            data,
            prompt_mode=data["mode"],
            tool_config=tool_config,
            tools=tools,
        )

        human_msg = await self.acreate_human_message(data)

        if use_checkpointer:
            # Checkpointer 模式下仍执行注入检测和预算分配
            self._apply_context_engineering_for_checkpointer(
                user_message=data.get("message", ""),
                model_name=data.get("model_name"),
                mode=data.get("mode", "agent"),
            )

            # 业务等待恢复模式（wait_for_subagent 挂起后子代理终态唤醒）：
            # graph_input 为执行器注入的 Command(resume={interrupt_id: {...}})，
            # 从 checkpoint 续跑（不发新消息）；必须跳过下方 pending interrupt
            # 自动拒绝清理——wait interrupt 正处于 pending，误拒会导致
            # wait_for_subagent 收到非法 resume 值（"子代理结果缺失"）。
            resume_command = data.get("resume_command")
            if resume_command is not None:
                graph_input = resume_command
                config = thread_config
                logger.info("[ChatExec] 业务等待恢复模式：以 Command(resume) 续跑（跳过 pending interrupt 清理）")
            else:
                # 检查 checkpoint 中是否有 pending interrupt，如果有则自动拒绝
                # 避免用户在有 pending interrupt 时发新消息导致状态损坏
                # （session_id：审批行定位键，与创建点 ctx.session_id（data["session_id"]
                #  or "" 归一）同源，阶段 7 票①）
                await self._auto_reject_pending_interrupts(
                    agent, thread_config, session_id=data.get("session_id") or ""
                )

                # 1.2：graph_input 仅含本轮 human 消息。记忆注入由
                # MemoryInjectionMiddleware（AgentService 按 use_checkpointer 装配）
                # 在每次模型调用前 request.override 动态前插，不写回 checkpoint
                # state（旧实现把研究上下文 + 记忆注入 SystemMessage 前置进
                # graph_input，随 checkpoint 持久化且逐轮堆积）。研究上下文不经
                # 本通道：AgentService 已将 data["_research_system_prompt"] 作为
                # AgentConfig.system_prompt 注入（模型调用时拼接，同样不进 state）。
                graph_input = {"messages": [human_msg]}
                config = thread_config
        else:
            chat_history = data.get("chat_history", [])
            user_message = data.get("message", "")
            model_name_for_budget = data.get("model_name")

            # 1.5 注入检测仅留痕（无隔离动作）：用户消息原文仍进入上下文工程与
            # 模型上下文。非受信内容的消毒发生在 ContextManager._build_base_context
            # （sanitize_user_input 以 user_input 信封包裹 query 后进结构化上下文），
            # 此处不改写用户消息——改写会破坏落库/前端展示与对话历史一致性。
            # 旧日志"用户输入将被隔离"为假隔离表述，已改为真实语义。
            if self._context_service.check_injection(user_message):
                logger.warning("检测到指令注入尝试（已留痕，未隔离；用户输入原样进入上下文工程）")

            chat_history, ce_metadata = await self.aapply_context_engineering(
                chat_history,
                user_message,
                mode=data.get("mode", "agent"),
                model_name=model_name_for_budget,
            )
            # 4.4/4.8：质量门禁降级摘要与最终裁剪转用户可见提示（degradation
            # 事件经 _process_normal_stream_chat 冲刷广播，前端 type=degradation）
            for degradation_event in build_context_degradation_events(ce_metadata):
                data.setdefault("_stream_degradation_events", []).append(degradation_event)
            langchain_chat_history = convert_chat_history(chat_history)
            messages = list(langchain_chat_history) if langchain_chat_history else []
            # 非 Checkpointer 模式：将研究上下文作为 SystemMessage 注入到对话历史最前面
            if research_context:
                from langchain_core.messages import SystemMessage

                research_system_msg = SystemMessage(content=data["_research_system_prompt"])
                messages.insert(0, research_system_msg)
            messages.append(human_msg)
            graph_input = {"messages": messages}
            # chat 单轮对话：工具循环步数适中（常量取值依据见 ai_engine/config.py）
            config = {"recursion_limit": AGENT_RECURSION_LIMIT_CHAT}

        return agent, graph_input, config, agent_config
