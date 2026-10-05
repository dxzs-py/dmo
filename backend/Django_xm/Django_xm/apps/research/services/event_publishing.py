"""深研实时事件发布边界模块（E6 三边界拆分：event_publishing）。

职责（audit B 组 §2.3-2）：
- ``DeepResearchEventPublisher``：深度研究链路实时事件发布的内聚单元，
  收编原 ``OfficialDeepAgentAdapter`` 巨型方法中的四段发布逻辑：
  1. 工具调用生命周期事件发布（``publish_tool_event``，原 adapter._publish_tool_event）
  2. 子代理工具事件转发回调（``make_tool_event_callback``，原 _on_tool_event 闭包）
  3. 子代理正文/思考转发回调（``make_subagent_content_callback``，原 _on_subagent_content 闭包）
  4. 主 agent reasoning / 正文 chunk 消费（``handle_reasoning_chunk`` /
     ``handle_content_chunk``，原 _process_chunk 内联广播段，先推理后正文顺序保持）

会话级累计容器（accumulated_reasoning / _main_content / subagent_contents /
_tool_entries）保留在 adapter 上（任务完成/挂起时经 research_runner → writeback
落库的权威来源），发布器经 owner 引用读写，不复制状态。

注入缝保持：``adapter._publish_tool_event`` 保留为实例级委托入口
（test_session_budget 等测试以实例属性替换该方法作为工具事件注入缝），
子代理回调与 _process_chunk 均经该入口发布。
"""

from typing import Any

from Django_xm.apps.ai_engine.services.thinking import extract_thinking_content
from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)


class DeepResearchEventPublisher:
    """深度研究实时事件发布器（持有 adapter 引用，读写会话级累计容器）。"""

    def __init__(self, owner: Any):
        # owner: OfficialDeepAgentAdapter——会话级状态权威来源
        # （chat_session_id/thread_id/accumulated_reasoning/_main_content/
        #   subagent_contents/_tool_entries）
        self._owner = owner

    async def publish_tool_event(self, evt: dict[str, Any]) -> None:
        """发布单个工具调用生命周期事件到实时频道。

        原样迁移自 ``OfficialDeepAgentAdapter._publish_tool_event``
        （adapter 保留实例级委托入口 ``_publish_tool_event``）。

        收敛历史内联四步（register → bind_position → transition_async →
        aggregate_tool_entry）为单次 ``publish_tool_lifecycle_event(...)`` 调用，
        与 chat 主链路 / chat 子代理共享同一编排实现（参数兜底、PENDING 补发
        防护、position 绑定、条目聚合均由编排函数内置）。

        路由策略（与 writeback.broadcast_stream_completed 一致）：
        - chat 关联场景（owner.chat_session_id 非空）：session + task 双频道
          由 cross_module_id 触发，transition 从 context 透传给底层 publish_tool_call
        - 独立深度研究场景：仅 task 频道

        子 agent 嵌套层级字段（Phase E3）：
        evt 中的 parent_tool_call_id / depth / agent_name / risk_ceiling / description /
        subagent_thread_id（由 _on_tool_event 从 SubAgentToolEventMiddleware 透传）
        注册到 ToolCallContext 并经 transition 透传到事件 payload；主 agent 工具不携带
        （evt 中无对应 key）。agent_path 不再注册/透传（spec REMOVED：路由唯一依据
        subagent_thread_id）。

        Args:
            evt: ToolEventExtractor.feed 返回的事件 dict（或 _on_tool_event 构造），字段：
                - event_type: EventType (TOOL_CALL_PENDING / TOOL_CALL_COMPLETED / TOOL_CALL_FAILED)
                - tool_call_id: str
                - tool_name: str
                - parameters: dict
                - result: str (仅 COMPLETED)
                - error: str (仅 FAILED)
                - parent_tool_call_id/depth/agent_name/risk_ceiling/description/
                  subagent_thread_id: 子 agent 嵌套字段（可选）
        """
        from Django_xm.common.approval_utils import derive_cross_module_id_from_source
        from Django_xm.common.event_schema import EventSource, EventType
        from Django_xm.common.tool_event_publisher import publish_tool_lifecycle_event

        owner = self._owner
        event_type = evt.get("event_type")
        tool_call_id = evt.get("tool_call_id", "") or ""
        tool_name = evt.get("tool_name") or "unknown"
        parameters = evt.get("parameters") or {}

        logger.info(
            f"[OfficialDeepAgent] _publish_tool_event 入口: event_type={event_type}, "
            f"tool={tool_name}, tc_id={tool_call_id}, "
            f"params_keys={list(parameters.keys()) if isinstance(parameters, dict) else type(parameters).__name__}"
        )

        # event_type 必须为 EventType 枚举（提取器保证）
        # 防御性校验：跳过非法事件类型，避免 transition_async 内部抛 KeyError
        if not isinstance(event_type, EventType):
            logger.warning(
                f"[OfficialDeepAgent] 跳过非 EventType 事件: event_type={event_type!r}, tool_call_id={tool_call_id}"
            )
            return

        # 跨模块同步目标：chat 关联场景非空 → session + task 双频道
        cross_module_id = derive_cross_module_id_from_source("deep_research", owner.chat_session_id)

        # message_id：chat 关联场景优先从当前 langgraph 运行上下文读取
        # assistant_message_id（research_runner 在 config.configurable 中注入），
        # 回退实例属性。前端工具卡归属依赖 messageBackendId，缺失时聊天深度研究
        # 模式的工具卡片会消失（根因修复）。
        _assistant_message_id = ""
        if owner.chat_session_id:
            try:
                from langgraph.config import get_config as _get_config

                _cfg = _get_config()
                if isinstance(_cfg, dict):
                    _assistant_message_id = (
                        (_cfg.get("configurable") or {}).get("assistant_message_id") or ""
                    )
            except Exception:
                _assistant_message_id = ""
        if not _assistant_message_id:
            _assistant_message_id = getattr(owner, "_assistant_message_id", "")

        # 提取子 agent 嵌套层级字段（Phase E3）：evt 无对应 key 时取默认空值（主 agent）
        sub_parent_tool_call_id = evt.get("parent_tool_call_id", "") or ""
        sub_depth = evt.get("depth", 0)
        if not isinstance(sub_depth, int) or sub_depth < 0:
            sub_depth = 0
        sub_agent_name = evt.get("agent_name", "") or ""
        # risk_ceiling 统一转字符串（可能是 RiskLevel 枚举）
        sub_risk_ceiling_raw = evt.get("risk_ceiling")
        if sub_risk_ceiling_raw is not None and not isinstance(sub_risk_ceiling_raw, str):
            sub_risk_ceiling = (
                sub_risk_ceiling_raw.value if hasattr(sub_risk_ceiling_raw, "value") else str(sub_risk_ceiling_raw)
            )
        else:
            sub_risk_ceiling = sub_risk_ceiling_raw or ""
        # description：子 agent 角色描述（任务目标，Task 2.4），仅子 agent 事件携带
        sub_description = evt.get("description", "") or ""
        # subagent_thread_id：子代理 SSE 定向推送路由标识符（spec D10），仅子代理事件携带
        sub_subagent_thread_id = evt.get("subagent_thread_id", "") or ""

        # position 采集（按图层局部化，单一权威来源，D3）：
        # - 子代理（subagent_thread_id 非空）：该子代理图层已转发正文 content 长度
        # - 主 agent：主 agent 已输出累计正文 _main_content 长度
        # 传入编排函数经 service.bind_position 写入 context（仅补全空字段，永久不变）。
        if sub_subagent_thread_id:
            _sub_entry = owner.subagent_contents.get(sub_subagent_thread_id) or {}
            _position: int | None = len(_sub_entry.get("content") or "")
        else:
            _position = len(owner._main_content)

        # 唯一编排入口：register → bind_position → transition_async → aggregate_tool_entry
        # （参数权威源兜底、PENDING 补发防护、position 绑定、条目聚合均内置；
        #  entries 传 owner._tool_entries → 主/子代理统一落库条目聚合）。
        await publish_tool_lifecycle_event(
            module=EventSource.DEEP_RESEARCH,
            module_id=owner.thread_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            event_type=event_type,
            message_id=_assistant_message_id,
            parameters=parameters,
            result=evt.get("result"),
            error=evt.get("error"),
            cross_module_id=cross_module_id,
            parent_tool_call_id=sub_parent_tool_call_id,
            depth=sub_depth,
            agent_name=sub_agent_name,
            risk_ceiling=sub_risk_ceiling,
            description=sub_description,
            subagent_thread_id=sub_subagent_thread_id,
            entries=owner._tool_entries,
            position=_position,
        )

    def make_tool_event_callback(self):
        """构造子代理工具事件转发回调（原 astream 主方法 _on_tool_event 闭包原样迁移）。

        注入契约不变：回调写入 ``config["configurable"]["_on_tool_event"]``，
        SubAgentToolEventMiddleware（子 agent middleware）从 configurable 读取，
        父 config 经 langgraph ensure_config 自动传播到子 agent。
        """
        owner = self._owner

        async def _on_tool_event(
            event_type,
            tool_call_id,
            tool_name,
            **kwargs,
        ):
            """子 agent 工具事件转发回调。

            将子 agent 内部的工具调用事件转发到父 SSE 流，
            通过统一 ``service.transition_async`` 发布到实时频道。

            子 agent 嵌套层级字段（Phase E3）：
            SubAgentToolEventMiddleware 通过 kwargs 传递
            depth / risk_ceiling（agent_name 由回调自行读取），
            本回调透传到 evt dict，由 _publish_tool_event 注册到 ToolCallContext，
            最终经 transition_async 透传到事件 payload，前端 ToolCallCard 可展示
            嵌套层级（与父 agent 直接调用的工具行为一致）；agent_path 不透传
            （spec REMOVED：事件 payload 不再携带 agent_path）。
            """
            logger.info(
                f"[OfficialDeepAgent] _on_tool_event 入口: event_type={event_type}, "
                f"tool={tool_name}, tc_id={tool_call_id}, "
                f"has_params={bool(kwargs.get('parameters'))}, has_result={'result' in kwargs}"
            )
            evt: dict[str, Any] = {
                "event_type": event_type,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
            }
            # 透传 parameters / result / error
            # must-use is-not-None check: 空 dict {} 是 falsy 但仍是有效值，
            # "if kwargs.get('parameters'):" 会丢弃空 dict → evt 缺 key → _publish_tool_event 取到 {}
            if kwargs.get("parameters") is not None:
                evt["parameters"] = kwargs["parameters"]
            if "result" in kwargs and kwargs["result"] is not None:
                evt["result"] = kwargs["result"]
            if "error" in kwargs and kwargs["error"] is not None:
                evt["error"] = kwargs["error"]
            # 透传子 agent 嵌套层级字段（Phase E3）
            # 注意：不能使用 `val not in {"", 0}` 判断——list 参与 set 成员测试
            # 会抛 `unhashable type: 'list'`。改用 tuple 成员测试（in 逐元素 ==
            # 比较，list == "" / list == 0 均安全返回 False）。
            for sub_field in (
                "parent_tool_call_id",
                "depth",
                "agent_name",
                "risk_ceiling",
                # description：子 agent 角色描述（任务目标，Task 2.4），
                # 由 SubAgentToolEventMiddleware 从 nesting 透传，仅子 agent 事件携带
                "description",
                # subagent_thread_id：子代理 SSE 定向推送路由标识符（spec D10），
                # 由 SubAgentToolEventMiddleware 从 configurable 透传，仅子 agent 事件携带
                "subagent_thread_id",
            ):
                val = kwargs.get(sub_field)
                if val is None or val in ("", 0):
                    # 空字符串 / 0（depth=0 主 agent）不写入
                    continue
                evt[sub_field] = val
            await owner._publish_tool_event(evt)

        return _on_tool_event

    def make_subagent_content_callback(self):
        """构造子代理正文/思考转发回调（原 astream 主方法 _on_subagent_content 闭包原样迁移）。

        注入契约不变：回调写入 ``config["configurable"]["_on_subagent_content"]``，
        SubAgentContentMiddleware（子 agent middleware）从 configurable 读取，
        在子代理每次模型调用后转发其正文与中间思考到父 SSE 流（Agent 图层嵌套）。
        """
        owner = self._owner
        # 惰性初始化去重键集合（原主方法内联段，时点保持：每次 astream 装配回调时；
        # 同一 adapter 跨审批 resume 复用同一实例时幂等、不重置）。
        # 处理逻辑（spec MODIFIED：路由收敛）：
        # 1. 按 subagent_thread_id 累计到 owner.subagent_contents（废弃 agent_path
        #    拼接键），content/reasoning_content 追加式累积（多轮模型调用拼接）；
        #    按 msg_id 幂等去重——审批 interrupt 恢复时 LangGraph 重放节点，
        #    同一条 AIMessage 会再次触发回调，跳过已转发消息（防重复 + 防错位）；
        # 2. 发布 STREAM_SUBAGENT_CONTENT WebSocket 事件到 session/task 频道，
        #    前端按 subagent_thread_id 路由到对应子代理卡片实时展示
        #    （data 不携带 agent_path，仅保留 subagent_thread_id 定向路由）；
        # 3. 事件携带 message_id（chat 关联场景定位归属消息）。
        if not hasattr(owner, "_sent_subagent_msg_keys"):
            owner._sent_subagent_msg_keys: dict[str, set] = {}

        async def _on_subagent_content(
            agent_path,
            content,
            reasoning_content,
            agent_name,
            depth,
            subagent_thread_id="",
            msg_id="",
        ):
            """子代理正文/中间思考转发回调（spec MODIFIED：按 subagent_thread_id 路由累计）。"""
            if not subagent_thread_id:
                return
            from Django_xm.common.tool_call_aggregation import merge_subagent_content

            if merge_subagent_content(
                owner.subagent_contents,
                subagent_thread_id,
                content or "",
                reasoning_content or "",
                msg_id or "",
                owner._sent_subagent_msg_keys,
            ):
                logger.debug(
                    f"[OfficialDeepAgent] 跳过重放子代理正文: "
                    f"subagent_thread_id={subagent_thread_id}, msg_id={msg_id}"
                )
                return
            try:
                from Django_xm.common.event_schema import EventSource, EventType
                from Django_xm.common.realtime_events import publish_event

                _configurable = getattr(owner, "_last_config", None)
                _cfg = _configurable if isinstance(_configurable, dict) else {}
                _session_id = (_cfg.get("configurable") or {}).get("chat_session_id") or owner.chat_session_id
                _assistant_message_id = (_cfg.get("configurable") or {}).get("assistant_message_id") or ""
                if not _assistant_message_id:
                    _assistant_message_id = getattr(owner, "_assistant_message_id", "")

                # 深度研究执行流：session/task 双频道（与工具事件路由一致）
                await publish_event(
                    EventType.STREAM_SUBAGENT_CONTENT,
                    {
                        "source": EventSource.DEEP_RESEARCH,
                        "source_id": owner.thread_id,
                        "message_id": _assistant_message_id or None,
                        "data": {
                            # data 不携带 agent_path（spec MODIFIED：data.agent_path 废弃，
                            # 路由唯一依据顶层 subagent_thread_id，协议标识符保持
                            # snake_case，见 sessionTransformers 转换边界）
                            "content": content,
                            "reasoning_content": reasoning_content,
                            "agent_name": agent_name or "",
                            "depth": depth,
                        },
                    },
                    session_id=_session_id or None,
                    task_id=owner.thread_id,
                    subagent_thread_id=subagent_thread_id or None,
                )
            except Exception as e:
                logger.warning(
                    f"[OfficialDeepAgent] 广播子代理正文失败: "
                    f"subagent_thread_id={subagent_thread_id}, err={e}"
                )

        return _on_subagent_content

    def handle_reasoning_chunk(self, msg_obj: Any, config_arg: dict) -> None:
        """消费 messages 模式 chunk：提取 thinking/reasoning 并累积广播。

        原样迁移自 ``_process_chunk`` 内联段（先推理后正文的段序保持）。
        每次 chunk 追加到 owner.accumulated_reasoning，广播 content 为累积后的
        完整推理文本（前端 AiReasoning 覆盖语义下内容完整）；广播失败仅告警。
        """
        owner = self._owner
        # 捕获 LLM thinking/reasoning 内容（AIMessageChunk.additional_kwargs.reasoning_content）
        # 累积后广播完整内容：每次 chunk 追加到 accumulated_reasoning，
        # 广播 content 为累积后的完整推理文本（前端 AiReasoning 覆盖语义下内容完整）。
        thinking_text = extract_thinking_content(msg_obj)
        if thinking_text:
            owner.accumulated_reasoning = (owner.accumulated_reasoning or "") + thinking_text
            _configurable = config_arg.get("configurable", {})
            _session_id = _configurable.get("chat_session_id") or owner.chat_session_id
            _message_id = _configurable.get("assistant_message_id")
            if _session_id and _message_id:
                try:
                    from Django_xm.apps.research.services.writeback import (
                        broadcast_stream_reasoning,
                    )

                    broadcast_stream_reasoning(
                        session_id=_session_id,
                        task_id=owner.thread_id,
                        message_id=_message_id,
                        content=owner.accumulated_reasoning,
                    )
                except Exception as e:
                    logger.warning(
                        f"[OfficialDeepAgent] 广播推理内容失败: "
                        f"session={_session_id}, msg={_message_id}, err={e}"
                    )

    def handle_content_chunk(self, msg_obj: Any, config_arg: dict) -> None:
        """消费 messages 模式 chunk：主 agent 图层正文累计 + 实时广播。

        原样迁移自 ``_process_chunk`` 内联段。**仅累计 AIMessageChunk**——
        position 计算依赖该容器长度（publish_tool_event 读 owner._main_content），
        容器仍在 owner 上；广播失败仅告警。
        """
        owner = self._owner
        # 主 agent 图层累计正文（position 注入依据 + 过程信息权威源）：
        # messages 模式 chunk 含 AIMessageChunk（主 agent 模型输出）与
        # ToolMessage（工具返回文本）。**仅累计 AIMessageChunk**——
        # ToolMessage.content（如 spawn 返回的"子代理已启动..."）若混入
        # main_content，会同时污染：① 正文渲染（工具文本重复显示在
        # 主消息流）；② position 基准（len(_main_content) 掺入工具文本，
        # 与前端最终 content 错位 → 工具卡切段乱序，wait/子代理卡被排末尾）。
        # chat 链路同构实现（loop.py 用事件 content 累计，天然仅含 AI 文本）。
        from langchain_core.messages import AIMessageChunk

        _chunk_content = ""
        if isinstance(msg_obj, AIMessageChunk):
            _chunk_content = getattr(msg_obj, "content", None) or ""
            if _chunk_content:
                owner._main_content += _chunk_content
                # 实时广播主 agent 累计正文（对齐 chat 链路 chunk →
                # STREAM_CONTENT_UPDATE），使前端 content 非空、
                # position 内联工具卡生效（乱序根因修复）。节流由
                # broadcast_stream_content 内部处理。
                try:
                    from Django_xm.apps.research.services.writeback import (
                        broadcast_stream_content,
                    )

                    _cfg_for_content = config_arg.get("configurable", {})
                    _content_session_id = (
                        _cfg_for_content.get("chat_session_id") or owner.chat_session_id
                    )
                    _content_message_id = (
                        _cfg_for_content.get("assistant_message_id")
                        or getattr(owner, "_assistant_message_id", "")
                    )
                    broadcast_stream_content(
                        session_id=_content_session_id,
                        task_id=owner.thread_id,
                        message_id=_content_message_id,
                        content=owner._main_content,
                    )
                except Exception as _content_broadcast_err:
                    logger.warning(
                        f"[OfficialDeepAgent] 广播主 agent 正文失败: "
                        f"err={_content_broadcast_err}"
                    )
