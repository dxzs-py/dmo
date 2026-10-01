"""StreamContext — 流式循环可变状态封装

将 chat_service 中散布的流式状态变量统一封装为单一对象，
避免函数间长参数列表，确保两种模式（普通 / 深度思考）使用完全相同的状态结构。

工具调用的归一化与生命周期事件发布已收敛至唯一提取器
（``apps.tools.tool_event_extractor.ToolEventExtractor``）与唯一发布编排
（``common.tool_event_publisher.publish_tool_lifecycle_event``），
统一工具条目容器为执行 ``data["_tool_entries"]``（主/子代理共享），
本上下文不再持有工具调用映射 / 参数累积器 / 已用 ID 集合等状态。
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StreamContext:
    """流式循环的可变状态

    所有字段在循环内就地修改。current_message_content 为 str（不可变），
    通过 += 赋值更新（等价于属性赋值，dataclass 支持）。
    """

    # ── 工具调用计数（重复调用计数诊断）──
    tool_call_count: dict[str, int] = field(default_factory=dict)

    # ── 消息累积 ──
    current_message_content: str = ""
    all_messages: list[Any] = field(default_factory=list)

    # ── 推理状态 ──
    # accumulated_reasoning 同时承载 _stream_state 共享引用
    accumulated_reasoning: dict[str, Any] = field(default_factory=lambda: {"content": "", "_stream_state": None})
    has_sent_reasoning: bool = False
    has_model_reasoning: bool = False
    thinking_start_time: float = 0.0

    # ── 审批中断 ──
    interrupt_info: dict | None = None
    session_id: str = ""
    message_id: str = ""

    # ── 韧性重试 ──
    retry_count: int = 0

    # ── 其他 ──
    prefer_tool_result: bool = False
    fallback_triggered: bool = False

    def init_stream_state(self, stream_state) -> None:
        """初始化 _stream_state 引用（从 data._stream_state 传入）"""
        self.accumulated_reasoning["_stream_state"] = stream_state
