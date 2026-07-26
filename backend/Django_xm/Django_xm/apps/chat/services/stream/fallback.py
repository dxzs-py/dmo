"""FallbackStreamService — 无工具纯对话流式回退

无工具纯对话流式回退实现（_stream_without_tools + _clean_tool_call_messages）。
当 agent 模式（普通或深度思考）因 API 错误、超时等原因失败时，
回退到无工具的纯 LLM 对话模式，保证用户仍能获得回复。

两种模式（普通 / 深度思考）共用此回退服务，确保回退行为一致。
"""

import asyncio
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from langchain_core.messages import AIMessage, ToolMessage

from Django_xm.apps.ai_engine.models import SystemConfig
from Django_xm.apps.ai_engine.services.cost_tracker import TokenDetailTracker
from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler
from Django_xm.apps.chat.services.stream_helpers import (
    extract_thinking_content,
    update_usage_and_tokens,
)

logger = logging.getLogger(__name__)


class FallbackStreamService:
    """无工具纯对话流式回退服务"""

    def __init__(self, chat_service):
        """
        Args:
            chat_service: ChatService 实例，提供 _apply_context_engineering / _acreate_human_message
        """
        self._chat_service = chat_service

    @staticmethod
    def clean_tool_call_messages(messages: List) -> List:
        """清理消息历史中未完成的 tool_calls

        当 agent 执行失败回退到无工具模式时，对话历史中可能包含
        assistant message（含 tool_calls）但没有对应的 ToolMessage 响应。
        OpenAI API 要求每个 tool_call_id 都必须有对应的 ToolMessage，
        否则会报 400 错误。此方法移除这些未完成的 tool_calls。
        """
        if not messages:
            return messages

        responded_ids = set()
        for msg in messages:
            if isinstance(msg, ToolMessage):
                responded_ids.add(msg.tool_call_id)

        cleaned = []
        for msg in messages:
            if isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls:
                unresponded = [tc for tc in msg.tool_calls if tc.get('id') not in responded_ids]
                if unresponded:
                    if msg.content:
                        cleaned.append(AIMessage(content=msg.content))
                    continue
            if isinstance(msg, ToolMessage):
                cleaned.append(msg)
                continue
            cleaned.append(msg)

        return cleaned

    async def stream_without_tools(
        self,
        model_instance,
        data: Dict[str, Any],
        usage_tracker,
        token_detail_tracker: Optional[TokenDetailTracker] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """无工具纯对话流式

        Args:
            model_instance: LLM 模型实例
            data: 请求数据
            usage_tracker: Token 用量追踪器
            token_detail_tracker: Token 详情追踪器
        """
        from Django_xm.apps.chat.services.chat_service import convert_chat_history

        chat_history = data.get('chat_history', [])
        chat_history, _ce_metadata = self._chat_service._apply_context_engineering(
            chat_history, data.get('message', ''), mode=data.get('mode', 'agent'),
            model_name=data.get('model_name'),
        )
        langchain_chat_history = convert_chat_history(chat_history)

        messages = []
        if langchain_chat_history:
            messages.extend(langchain_chat_history)

        messages = self.clean_tool_call_messages(messages)

        human_msg = await self._chat_service._acreate_human_message(data)
        messages.append(human_msg)

        current_message_content = ""
        accumulated_reasoning: Dict[str, str] = {"content": ""}
        thinking_start_time = time.time()

        with TokenUsageCallbackHandler() as cb:
            from Django_xm.apps.ai_engine.services.llm_factory import FallbackDetectionCallback
            bound_model = getattr(model_instance, 'bound', model_instance)
            fb_callback = FallbackDetectionCallback(
                expected_provider=data.get('provider_id', '') or getattr(bound_model, '_provider_id', '') or '',
                expected_model=data.get('model_name', '') or getattr(bound_model, 'model_name', '') or getattr(bound_model, 'model', '') or '',
            )
            try:
                async for chunk in model_instance.astream(messages, config={"callbacks": [cb, fb_callback]}):
                    content = getattr(chunk, "content", "")
                    if content:
                        current_message_content += content
                        yield {"type": "chunk", "content": content}

                    # 统一思考内容提取（兼容 DeepSeek/Ollama/Anthropic）
                    thinking_text = extract_thinking_content(chunk)
                    if thinking_text:
                        prev = accumulated_reasoning.get("content", "") or ""
                        accumulated_reasoning["content"] = prev + thinking_text
                        yield {
                            "type": "reasoning",
                            "data": {
                                "content": accumulated_reasoning["content"],
                                "duration": 0,
                            },
                        }

                    await asyncio.sleep(0.01)
            except Exception as e:
                logger.error(f"无工具模式流式调用失败: {e}", exc_info=True)
                raise

        update_usage_and_tokens(cb, usage_tracker, token_detail_tracker)

        # 检测运行时 LLM fallback
        if fb_callback.fallback_detected:
            fallback_info = fb_callback.get_fallback_info()
            if fallback_info:
                yield {
                    'type': 'model_fallback',
                    'data': fallback_info,
                }
                try:
                    SystemConfig.set_value("default_chat_model", {
                        "provider_id": fallback_info["actual_provider"],
                        "model_name": fallback_info["actual_model"],
                    })
                except Exception:
                    pass

        thinking_duration = round(time.time() - thinking_start_time, 1)
        final_reasoning = (accumulated_reasoning.get("content") or "").strip()
        if final_reasoning:
            yield {
                "type": "reasoning",
                "data": {
                    "content": final_reasoning,
                    "duration": thinking_duration,
                },
            }
        else:
            yield {
                "type": "reasoning",
                "data": {
                    "content": f"深度思考完成，共思考了 {thinking_duration} 秒",
                    "duration": thinking_duration,
                },
            }
