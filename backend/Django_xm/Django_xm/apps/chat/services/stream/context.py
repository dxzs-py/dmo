"""StreamContext — 流式循环可变状态封装

将 chat_service 中散布的流式状态变量统一封装为单一对象，
避免函数间长参数列表，确保两种模式（普通 / 深度思考）使用完全相同的状态结构。

accumulated_reasoning / tool_calls_map / tool_call_count / tool_args_accumulator
为可变容器（dict），process_stream_chunk 在原地修改它们，无需额外同步。
"""

from dataclasses import dataclass, field


@dataclass
class StreamContext:
    """流式循环的可变状态

    所有字段在循环内就地修改。current_message_content 为 str（不可变），
    通过 += 赋值更新（等价于属性赋值，dataclass 支持）。
    """

    # ── 工具调用状态 ──
    tool_calls_map: dict[str, dict] = field(default_factory=dict)
    used_tool_call_ids: set = field(default_factory=set)
    tool_call_count: dict[str, int] = field(default_factory=dict)
    tool_args_accumulator: dict[str, str] = field(default_factory=dict)

    # ── 消息累积 ──
    current_message_content: str = ""
    all_messages: list = field(default_factory=list)

    # ── 推理状态 ──
    # accumulated_reasoning 同时承载 _stream_state 共享引用，
    # 供 views_chat.py finally 块写入数据库
    accumulated_reasoning: dict[str, str] = field(
        default_factory=lambda: {"content": "", "_stream_state": None}
    )
    has_sent_reasoning: bool = False
    has_model_reasoning: bool = False
    thinking_start_time: float = 0.0

    # ── 审批中断 ──
    interrupt_info: dict | None = None

    # ── 韧性重试 ──
    retry_count: int = 0

    # ── 其他 ──
    prefer_tool_result: bool = False
    fallback_triggered: bool = False

    def init_stream_state(self, stream_state) -> None:
        """初始化 _stream_state 引用（从 data._stream_state 传入）"""
        self.accumulated_reasoning["_stream_state"] = stream_state
