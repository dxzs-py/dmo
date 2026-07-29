"""StreamStrategy — 流式模式策略

封装普通 agent 模式与深度思考模式的差异点。
两种模式共用 run_stream_loop 核心循环，差异通过策略对象注入：

| 差异点               | NormalStreamStrategy       | DeepThinkingStreamStrategy      |
|---------------------|----------------------------|---------------------------------|
| enable_deep_thinking| False                      | True                            |
| reasoning_source    | "model_intrinsic"          | "deep_thinking"                 |
| on_loop_start       | 空                         | 发送"正在深度思考中..."         |
| on_loop_success     | 仅当有推理内容时发送        | 总是发送（内容或完成提示）       |

handle_special_event（tool_usage_dedup/blocked）两种模式行为一致，
放在 BaseStreamStrategy 中共享，修复深度思考模式原缺失的工具用量限制处理。
"""

import logging
import time
from collections.abc import AsyncGenerator

from .context import StreamContext

logger = logging.getLogger(__name__)


class BaseStreamStrategy:
    """策略基类：共享 handle_special_event 逻辑"""

    enable_deep_thinking: bool = False
    reasoning_source: str = "model_intrinsic"

    async def on_loop_start(self, ctx: StreamContext, data: dict) -> AsyncGenerator[dict, None]:
        """循环开始前的事件流（普通模式为空，深度思考模式发送初始 reasoning）"""
        return
        yield  # 使函数成为 async generator

    def handle_special_event(self, event: dict, ctx: StreamContext) -> list[dict] | None:
        """处理 tool_usage_dedup / tool_usage_blocked / reasoning 等特殊事件

        Returns:
            None → 透传原始事件
            List[dict] → 替换为这些事件（不透传原始事件）
            [] → 消费原始事件（不 yield 任何事件）
        """
        event_type = event.get("type")

        # tool_usage_dedup：记录日志，不推送前端
        if event_type == "tool_usage_dedup":
            tool_info = event.get("data", {})
            short_msg = tool_info.get("short_circuit_response", "")
            logger.info(f"[Stream] 工具 {tool_info.get('tool_name')} 被去重: {short_msg}")
            return []

        # tool_usage_blocked：渐进式阻断，记录警告，以 chunk 形式通知前端
        if event_type == "tool_usage_blocked":
            tool_info = event.get("data", {})
            short_msg = tool_info.get("short_circuit_response", "")
            logger.warning(f"[Stream] 工具 {tool_info.get('tool_name')} 被阻断: {short_msg}")
            chunk_content = f"\n\n[系统提示] {short_msg}\n"
            ctx.current_message_content += chunk_content
            return [{"type": "chunk", "content": chunk_content}]

        # reasoning 事件：跟踪模型是否产生推理内容
        if event_type == "reasoning":
            if not ctx.has_sent_reasoning:
                ctx.has_sent_reasoning = True
            ctx.has_model_reasoning = True
            return None  # 透传

        return None  # 其他事件透传

    async def on_loop_success(self, ctx: StreamContext, data: dict) -> AsyncGenerator[dict, None]:
        """循环成功后的预处理事件（reasoning 完成事件）"""
        return
        yield  # 使函数成为 async generator


class NormalStreamStrategy(BaseStreamStrategy):
    """普通 agent 模式策略"""

    enable_deep_thinking = False
    reasoning_source = "model_intrinsic"

    async def on_loop_success(self, ctx: StreamContext, data: dict) -> AsyncGenerator[dict, None]:
        """普通模式：仅当 _enable_deep_thinking 且有模型自带推理内容时发送 reasoning 完成事件"""
        if (
            ctx.accumulated_reasoning
            and ctx.accumulated_reasoning.get("content", "").strip()
            and data.get("_enable_deep_thinking")
        ):
            yield {
                "type": "reasoning",
                "data": {
                    "content": ctx.accumulated_reasoning["content"].strip(),
                    "duration": 0,
                    "source": "model_intrinsic",
                    "finished": True,
                },
            }


class DeepThinkingStreamStrategy(BaseStreamStrategy):
    """深度思考模式策略"""

    enable_deep_thinking = True
    reasoning_source = "deep_thinking"

    async def on_loop_start(self, ctx: StreamContext, data: dict) -> AsyncGenerator[dict, None]:
        """深度思考模式：发送初始 reasoning 事件"""
        ctx.has_sent_reasoning = True
        ctx.thinking_start_time = time.time()
        yield {
            "type": "reasoning",
            "data": {
                "content": "正在深度思考中...",
                "duration": 0,
                "source": "deep_thinking",
            },
        }

    async def on_loop_success(self, ctx: StreamContext, data: dict) -> AsyncGenerator[dict, None]:
        """深度思考模式：总是发送 reasoning 完成事件（内容或完成提示）"""
        thinking_duration = round(time.time() - ctx.thinking_start_time, 1) if ctx.thinking_start_time else 0
        final_reasoning = (ctx.accumulated_reasoning.get("content") or "").strip()

        if ctx.has_model_reasoning and final_reasoning:
            yield {
                "type": "reasoning",
                "data": {
                    "content": final_reasoning,
                    "duration": thinking_duration,
                    "source": "deep_thinking",
                },
            }
        elif ctx.has_sent_reasoning or not ctx.has_model_reasoning:
            yield {
                "type": "reasoning",
                "data": {
                    "content": f"深度思考完成，共思考了 {thinking_duration} 秒",
                    "duration": thinking_duration,
                    "source": "deep_thinking",
                },
            }
