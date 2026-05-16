"""
深度模式聊天服务

从 chat_service.py 拆分出的深度思考/深度研究相关逻辑：
- 深度思考流式处理
- 深度研究任务执行
- 无工具回退模式
"""
import asyncio
import time
import uuid
import logging
from typing import Dict, Any, List, Optional, AsyncGenerator

from langchain_core.messages import AIMessage, HumanMessage

from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler
from Django_xm.apps.ai_engine.services.cost_tracker import CostTracker
from Django_xm.apps.research.services import create_research_agent
from Django_xm.apps.research.services.deep_agent import should_use_deep_research
from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model
from .stream_helpers import (
    process_stream_chunk,
    build_context_info,
    update_usage_and_cost,
    finalize_tool_calls,
)
from ..utils import convert_chat_history, _lcp_len, extract_suggestions

logger = logging.getLogger(__name__)


class DeepChatService:
    """深度模式聊天服务"""

    def __init__(self, chat_service):
        self._chat_service = chat_service

    async def process_deep_thinking_stream(
        self,
        data: Dict[str, Any],
        usage_tracker,
        cost_tracker: Optional[CostTracker] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        tools = await self._chat_service._get_tools(data)
        model_instance = self._chat_service._resolve_model_instance(data)
        provider_id = data.get('provider_id')

        agent, thread_config, use_checkpointer = await self._chat_service._create_agent_with_memory(
            data, tools=tools, prompt_mode='deep-thinking', model_instance=model_instance,
        )

        human_msg = self._chat_service._create_human_message(data)

        if use_checkpointer:
            graph_input = {"messages": [human_msg]}
            config = thread_config
        else:
            chat_history = data.get('chat_history', [])
            langchain_chat_history = convert_chat_history(chat_history)
            messages = list(langchain_chat_history) if langchain_chat_history else []
            messages.append(human_msg)
            graph_input = {"messages": messages}
            config = {"recursion_limit": 50}

        current_message_content = ""
        all_messages = []
        tool_calls_map: Dict[str, Dict] = {}
        tool_call_count: Dict[str, int] = {}
        accumulated_reasoning: Dict[str, str] = {"content": ""}
        tool_args_accumulator: Dict[str, str] = {}
        thinking_start_time = time.time()
        has_sent_reasoning = False

        yield {
            "type": "reasoning",
            "data": {
                "content": "正在深度思考中，分析问题的多个维度...",
                "duration": 0,
            },
        }
        has_sent_reasoning = True

        with TokenUsageCallbackHandler() as cb:
            config["callbacks"] = [cb]
            try:
                async for chunk in agent.graph.astream(graph_input, config=config, stream_mode="messages"):
                    all_messages.append(chunk if not isinstance(chunk, tuple) else chunk[0])

                    try:
                        events = process_stream_chunk(
                            chunk, tool_calls_map, current_message_content,
                            tool_call_count=tool_call_count,
                            lcp_func=_lcp_len,
                            accumulated_reasoning=accumulated_reasoning,
                            tool_args_accumulator=tool_args_accumulator,
                        )
                    except Exception as chunk_err:
                        logger.warning(f"处理流式 chunk 失败: {chunk_err}")
                        continue

                    for event in events:
                        if event.get("type") == "chunk":
                            current_message_content += event.get("content", "")
                        yield event

                    await asyncio.sleep(0.01)
            except Exception as stream_err:
                if provider_id and model_instance and tools:
                    logger.warning(
                        f"深度思考模式模型 {provider_id} agent模式执行失败，回退到无工具纯对话模式: {type(stream_err).__name__}: {stream_err}"
                    )
                    async for fallback_event in self._stream_without_tools(
                        model_instance, data, usage_tracker, cost_tracker
                    ):
                        yield fallback_event
                    return
                logger.error(f"deep-thinking astream 执行异常: {type(stream_err).__name__}: {stream_err}", exc_info=True)
                raise

        update_usage_and_cost(cb, usage_tracker, cost_tracker)

        for tool_update_event in finalize_tool_calls(all_messages, tool_calls_map, tool_args_accumulator):
            yield tool_update_event

        thinking_duration = round(time.time() - thinking_start_time, 1)

        if has_sent_reasoning:
            yield {
                "type": "reasoning",
                "data": {
                    "content": f"深度思考完成，共思考了 {thinking_duration} 秒",
                    "duration": thinking_duration,
                },
            }

    async def run_deep_research_task(
        self, query: str, session_id: Optional[str] = None,
        usage_tracker=None, cost_tracker=None,
    ) -> Dict[str, Any]:
        thread_id = f"deep_{uuid.uuid4().hex[:12]}"

        def _task():
            agent = create_research_agent(
                thread_id=thread_id,
                enable_web_search=True,
                enable_doc_analysis=False,
                user_id=self._chat_service.user_id,
                session_id=session_id,
            )
            with TokenUsageCallbackHandler() as cb:
                result = agent.research(query)
            result['_usage'] = {
                'prompt_tokens': cb.prompt_tokens,
                'completion_tokens': cb.completion_tokens,
                'total_cost': cb.total_cost,
            }
            return result

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _task)

        usage_data = result.pop('_usage', {})
        if usage_data and usage_tracker and cost_tracker:
            usage_tracker.add_input_tokens(usage_data.get('prompt_tokens', 0))
            usage_tracker.add_output_tokens(usage_data.get('completion_tokens', 0))
            cost_tracker.update_from_metadata(
                {'usage_metadata': {
                    'input_tokens': usage_data.get('prompt_tokens', 0),
                    'output_tokens': usage_data.get('completion_tokens', 0),
                }}
            )
            cost_tracker.finish_record()

        return result

    async def _stream_without_tools(
        self,
        model_instance,
        data: Dict[str, Any],
        usage_tracker,
        cost_tracker: Optional[CostTracker] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        chat_history = data.get('chat_history', [])
        chat_history = self._chat_service._apply_compaction(chat_history)
        langchain_chat_history = convert_chat_history(chat_history)

        messages = []
        if langchain_chat_history:
            messages.extend(langchain_chat_history)

        human_msg = self._chat_service._create_human_message(data)
        messages.append(human_msg)

        current_message_content = ""

        with TokenUsageCallbackHandler() as cb:
            try:
                async for chunk in model_instance.astream(messages):
                    content = getattr(chunk, "content", "")
                    if content:
                        current_message_content += content
                        yield {"type": "chunk", "content": content}
                    await asyncio.sleep(0.01)
            except Exception as e:
                logger.error(f"无工具模式流式调用失败: {e}", exc_info=True)
                raise

        update_usage_and_cost(cb, usage_tracker, cost_tracker)
