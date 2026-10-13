"""chat 子代理流式回调域（阶段 5 自 chat_service 拆分，行为等价纯移动）。

职责（单一变化方向）：子代理正文/工具事件的转发回调、全量快照节流广播、
运行期落库冲刷、回调装配到 config.configurable。

拆分形式（patch 面约束定形，见 .trae/memory/2026-10-06.md 阶段 5 节）：
ChatService 经 mixin 继承承载本域方法——类级 patch
``mock.patch.object(ChatService, M)`` 在 ChatService 类上设置属性即可
shadow mixin 同名方法（MRO 先命中类自身），既有测试锚定零破坏。

依赖均为**函数内 import**（event_schema / realtime_events / stream_throttle /
tool_event_publisher / tool_call_aggregation / stream_persistence），测试 patch
打在源模块命名空间，与方法体所在模块位置无关。
"""

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# 子代理正文快照广播节流间隔（阶段 2，对齐 D2 reasoning/content 500ms 感知阈值）：
# payload 为累积全量快照（前端覆盖渲染），窗口内多帧合流为最新单帧，丢中间帧安全。
SUBAGENT_CONTENT_BROADCAST_INTERVAL = 0.5


@dataclass
class _SubagentStreamState:
    """子代理流式会话状态（跨回调共享的最小捕获状态包）。

    contents / tool_entries / sent_msg_keys 与 data["_subagent_contents"] 等
    共享同一 dict 引用，回调间语义与拆分前的局部变量闭包捕获保持一致。
    """

    session_id: str
    message_id: str
    contents: dict[str, dict[str, str]]
    tool_entries: dict[str, dict]
    sent_msg_keys: dict[str, set]


class SubagentStreamMixin:
    """ChatService 子代理流式回调域 mixin（方法体自 chat_service.py 原样迁入）。"""

    def _init_subagent_stream_state(self, data: dict[str, Any]) -> _SubagentStreamState:
        """初始化子代理流式会话状态（含业务等待恢复基线）。"""
        _subagent_contents: dict[str, dict[str, str]] = {}
        # 业务等待恢复模式：以挂起前已持久化的 subagent_contents 作为基线，
        # 恢复轮在此基础上继续累计（子代理工具 position 采集与最终落库依据）。
        if data.get("resume_subagent_contents"):
            _subagent_contents = dict(data["resume_subagent_contents"])
        data["_subagent_contents"] = _subagent_contents
        # 统一工具条目容器（tool_call_id → entry）：主代理与子代理共用同一 dict，
        # 作为落库权威来源。主代理工具由 loop._process_chunk 经
        # publish_tool_lifecycle_event 聚合写入；子代理工具由
        # _handle_subagent_tool_event 写入。若子代理工具仅依赖审批重建路径落库会
        # 丢失图层字段（subagent_thread_id 等）且无审批的 SAFE 工具（fs_list_files
        # 等）完全丢失 → 刷新后子代理卡片归集失败（乱序根源）。
        # 与 _subagent_contents 同构：闭包跨审批 resume 持续，挂起前由
        # persist_chat_tool_calls 落库，恢复轮经增量合并（按 id）不丢历史。
        _tool_entries: dict[str, dict] = {}
        # 业务等待恢复模式：以挂起前已持久化的工具条目作为基线，
        # 恢复轮经增量合并（按 id）继续演进，避免挂起后新增的自动通过工具卡
        # （无审批记录）无数据源可落库、子代理工具图层字段丢失。
        if data.get("resume_tool_entries"):
            _tool_entries = dict(data["resume_tool_entries"])
        data["_tool_entries"] = _tool_entries
        # 子代理正文快照节流器容器（阶段 2）：subagent_thread_id → TimeWindowThrottle。
        # 按线程独立（并发多子代理互不覆盖）；闭包跨审批 resume 持续，与
        # _subagent_contents 同生命周期。payload 为累积全量快照（前端覆盖渲染），
        # 窗口内合流 + persist_history=False 不入回放历史 + 广播时同步运行期落库。
        data["_subagent_content_throttles"] = {}
        # 子代理消息幂等键集合（按 subagent_thread_id 分组）：审批 interrupt
        # 恢复时 LangGraph 重放节点，SubAgentContentMiddleware 会对同一条
        # AIMessage 再次触发回调；跳过已转发的消息，否则子代理卡片内容
        # 重复且 tool_call position（按累计正文长度绑定）错位。
        # 闭包集合随回调对象跨多次 resume 持续存在（configurable 持同一引用）。
        return _SubagentStreamState(
            session_id=data.get("session_id", ""),
            message_id=str(data.get("_assistant_message_id") or data.get("message_id", "")),
            contents=_subagent_contents,
            tool_entries=_tool_entries,
            sent_msg_keys={},
        )

    async def _flush_subagent_snapshot(
        self, data: dict[str, Any], state: _SubagentStreamState
    ) -> None:
        """子代理数据实时落库（挂起前基线缺失的兜底修复）。

        业务等待挂起落库（persist_chat_tool_calls）发生在子代理执行前，
        _subagent_contents / 统一工具条目容器为空；若仅靠挂起/结束
        落库，子代理正文与图层字段（subagent_thread_id 等）中途丢失，
        恢复轮从 DB 读不到基线 → 刷新后子代理卡归集失败、子代理工具卡
        混入主切段乱序。每次子代理工具/正文事件聚合后调用，增量合并幂等。
        """
        from Django_xm.apps.chat.services.stream_persistence import persist_chat_tool_calls

        _cs_ref = data.get("_content_state_ref") or {}
        try:
            await persist_chat_tool_calls(data, _cs_ref, state.session_id, state.message_id)
        except Exception as _e:
            logger.debug(f"[ChatExec] 子代理快照落库失败: err={_e}")

    async def _flush_subagent_content_throttles(
        self, data: dict[str, Any], subagent_thread_id: str | None = None
    ) -> None:
        """冲刷子代理正文节流窗口（阶段 2，D2 边界红线同构）。

        冲刷时机对齐 D2 白名单规则：TOOL_CALL 类事件（position 内联保序，
        正文快照必须先于工具卡事件送达）冲刷同线程窗口；APPROVAL/挂起/终态
        （finalize）冲刷全部窗口——这些边界后流形态切换或执行结束，pending
        若不冲刷将永久丢失（最终态必须送达）。幂等：pending 为空时 no-op。

        Args:
            data: 执行数据包（节流器容器 ``data["_subagent_content_throttles"]``）。
            subagent_thread_id: 仅冲刷指定线程窗口；None 时冲刷全部（终态边界）。
        """
        throttles = data.get("_subagent_content_throttles") or {}
        if not throttles:
            return
        if subagent_thread_id:
            targets = [throttles[subagent_thread_id]] if subagent_thread_id in throttles else []
        else:
            targets = list(throttles.values())
        for throttle in targets:
            try:
                await throttle.flush()
            except Exception as _e:
                logger.warning(f"[ChatExec] 冲刷子代理正文节流窗口失败: err={_e}")

    async def _handle_subagent_tool_event(
        self,
        data: dict[str, Any],
        state: _SubagentStreamState,
        event_type,
        tool_call_id,
        tool_name,
        **kwargs,
    ) -> None:
        """chat 模式子代理工具事件转发回调（spec D1/D2）。

        仅接收真子代理事件（SubAgentToolEventMiddleware 按 subagent_thread_id
        非空转发）；主 agent 工具事件不经本回调（主链路唯一发布与持久化）。
        发布经唯一编排函数 ``publish_tool_lifecycle_event``（register →
        bind_position → transition_async → 聚合条目），聚合落库统一容器
        ``data["_tool_entries"]``（主/子代理共享）。
        """
        from Django_xm.common.event_schema import EventSource
        from Django_xm.common.event_schema import EventType as _ET
        from Django_xm.common.tool_event_publisher import publish_tool_lifecycle_event

        if not tool_call_id:
            return
        parameters = kwargs.get("parameters") or {}
        if not isinstance(parameters, dict):
            parameters = {}
        depth = kwargs.get("depth", 0)
        if not isinstance(depth, int) or depth < 0:
            depth = 0
        subagent_thread_id = kwargs.get("subagent_thread_id") or ""
        # position 采集（按图层局部化）：仅真子代理（spec D1：subagent_thread_id
        # 非空）执行——子代理工具 = 该子代理图层已累计正文长度
        # （_subagent_contents 按 subagent_thread_id 键累计，spec MODIFIED）
        position: int | None = None
        if subagent_thread_id:
            position = len((state.contents.get(subagent_thread_id) or {}).get("content") or "")

        # TOOL_CALL 边界冲刷（阶段 2，D2 白名单规则对齐）：先冲刷该子代理
        # 正文节流窗口，保证前端先收到全量正文快照再收到工具事件——position
        # 内联切段依赖"正文在前、工具卡在后"的到达顺序（保序红线）。
        await self._flush_subagent_content_throttles(data, subagent_thread_id)

        try:
            await publish_tool_lifecycle_event(
                module=EventSource.CHAT,
                module_id=state.session_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                event_type=event_type if isinstance(event_type, _ET) else _ET(event_type),
                message_id=state.message_id,
                parameters=parameters,
                result=kwargs.get("result"),
                error=kwargs.get("error"),
                parent_tool_call_id=kwargs.get("parent_tool_call_id") or "",
                depth=depth,
                agent_name=kwargs.get("agent_name") or "",
                description=kwargs.get("description") or "",
                subagent_thread_id=subagent_thread_id,
                entries=state.tool_entries,
                position=position,
            )
        except Exception as _e:
            logger.warning(
                f"[ChatExec] 子代理工具事件转发失败: tool={tool_name}, "
                f"tc_id={tool_call_id}, err={_e}"
            )
            return
        # 实时落库：子代理工具条目含图层字段（subagent_thread_id 等），
        # 挂起/恢复轮需从 DB 读到基线，否则刷新后归集失败（乱序根源）。
        await self._flush_subagent_snapshot(data, state)

    async def _handle_subagent_content(
        self,
        data: dict[str, Any],
        state: _SubagentStreamState,
        agent_path,
        content,
        reasoning_content,
        agent_name,
        depth,
        subagent_thread_id: str = "",
        msg_id: str = "",
    ) -> None:
        """chat 模式子代理正文/中间思考转发回调（spec MODIFIED：D10 路由收敛）。

        累计与路由唯一依据为 ``subagent_thread_id``（agent_path 仅保留为
        回调展示元数据参数，不进入事件 payload / 累计键）。
        按 ``msg_id`` 幂等：同一 AIMessage 只转发一次（重放去重）。

        阶段 2（eliminate-priority-debts，BREAKING）：payload 从增量追加语义
        改为**累积全量正文快照**（data.content / reasoning_content 为该子代理
        累计全文，前端覆盖渲染），经 ``TimeWindowThrottle`` 按 subagent_thread_id
        独立节流（500ms 合流，对齐 D2 感知阈值），``persist_history=False``
        不占用回放历史席位（E9 遗留缺口治理）；广播时同步运行期落库。
        """
        if not subagent_thread_id:
            return
        from Django_xm.common.tool_call_aggregation import merge_subagent_content

        _dup = merge_subagent_content(
            state.contents,
            subagent_thread_id,
            content or "",
            reasoning_content or "",
            msg_id or "",
            state.sent_msg_keys,
        )
        if _dup:
            logger.debug(
                f"[ChatExec] 跳过重放子代理正文: subagent_thread_id={subagent_thread_id}, msg_id={msg_id}"
            )
            return
        # 累积全量正文快照（覆盖语义）：取容器当前累计值构造 payload，
        # 经节流器合流（窗口内仅保留最新快照，丢中间帧不影响最终态）。
        _entry = state.contents.get(subagent_thread_id) or {}
        _snapshot = {
            "content": _entry.get("content") or "",
            "reasoning_content": _entry.get("reasoning_content") or "",
            "agent_name": agent_name or "",
            "depth": depth,
        }
        try:
            from Django_xm.common.event_schema import EventSource, EventType
            from Django_xm.common.realtime_events import publish_event
            from Django_xm.common.stream_throttle import TimeWindowThrottle

            throttles = data.setdefault("_subagent_content_throttles", {})
            throttle = throttles.get(subagent_thread_id)
            if throttle is None:

                async def _broadcast_subagent_snapshot(snapshot_data: dict) -> None:
                    """广播单条全量快照 + 运行期落库（节流窗口到期时触发）。"""
                    await publish_event(
                        EventType.STREAM_SUBAGENT_CONTENT,
                        {
                            "source": EventSource.CHAT,
                            "source_id": state.session_id,
                            "message_id": state.message_id or None,
                            "data": dict(snapshot_data),
                        },
                        session_id=state.session_id,
                        subagent_thread_id=subagent_thread_id or None,
                        # 快照仅实时广播不入历史（D2/PF-02 同源治理）：回放窗口
                        # 席位留给 tool_call/approval 等生命周期事件；恢复权威源
                        # 为落库 subagent_contents + message_updated/快照校对。
                        persist_history=False,
                    )
                    # 运行期落库（与广播同频合流）：挂起/刷新恢复轮需从 DB 读到
                    # subagent_contents 基线，否则子代理正文丢失（信息错位）。
                    await self._flush_subagent_snapshot(data, state)

                throttle = TimeWindowThrottle(
                    min_interval=SUBAGENT_CONTENT_BROADCAST_INTERVAL,
                    broadcaster=_broadcast_subagent_snapshot,
                )
                throttles[subagent_thread_id] = throttle

            await throttle.submit(_snapshot)
        except Exception as _e:
            logger.warning(
                f"[ChatExec] 广播子代理正文失败: subagent_thread_id={subagent_thread_id}, err={_e}"
            )

    def _wire_subagent_callbacks(
        self,
        config: dict[str, Any],
        data: dict[str, Any],
        state: _SubagentStreamState,
    ) -> None:
        """装配子代理事件回调到 config.configurable（闭包仅捕获 data/state）。"""

        async def _on_subagent_tool_event(event_type, tool_call_id, tool_name, **kwargs):
            await self._handle_subagent_tool_event(
                data, state, event_type, tool_call_id, tool_name, **kwargs
            )

        async def _on_subagent_content(
            agent_path,
            content,
            reasoning_content,
            agent_name,
            depth,
            subagent_thread_id="",
            msg_id="",
        ):
            await self._handle_subagent_content(
                data,
                state,
                agent_path,
                content,
                reasoning_content,
                agent_name,
                depth,
                subagent_thread_id,
                msg_id,
            )

        if isinstance(config, dict):
            config["configurable"]["_on_tool_event"] = _on_subagent_tool_event
            config["configurable"]["_on_subagent_content"] = _on_subagent_content
            # 子代理审批/事件路由必需字段：spawn 工具将父 configurable 继承给子代理，
            # langgraph_adapter._create_approvals_from_interrupts 从中取 chat_session_id
            # 创建审批（source=chat 时 request_approval_async 强制校验，缺失即拒绝创建
            # → 子代理挂起无审批按钮）。assistant_message_id 用于事件路由精确定位消息。
            config["configurable"]["chat_session_id"] = state.session_id
            config["configurable"]["assistant_message_id"] = state.message_id
