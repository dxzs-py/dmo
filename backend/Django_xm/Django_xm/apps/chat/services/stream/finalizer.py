"""finalize_stream — 循环后统一处理

两种模式（普通 / 深度思考）共用此模块，保证循环成功后行为完全一致：

1. update_usage_and_tokens + LLM fallback 检测
2. tool_usage 统计
3. 审批中断收尾（调用 finalize_interrupt）
4. 深度思考兜底（content 为空时用 reasoning）
5. strategy.on_loop_success（reasoning 完成事件）
6. finalize_tool_calls（正常路径）
7. _finalize_stream_response（补发 + 补全检查 + 建议生成）

修复的 bug：
- 深度思考模式原不调用 _finalize_stream_response（无建议生成、无补全检查）
- 工具结果补发原使用硬编码工具名，现改为工具元数据（output_to_chat / raw_content）驱动
"""

import logging
from typing import Any, AsyncGenerator, Dict, List

from langchain_core.messages import AIMessage

from Django_xm.apps.ai_engine.models import SystemConfig
from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model
from Django_xm.apps.chat.services.stream_helpers import (
    finalize_tool_calls,
    update_usage_and_tokens,
)
from Django_xm.apps.chat.utils import _needs_completion, extract_suggestions
from Django_xm.apps.tools.base import get_tool_metadata

from .context import StreamContext
from .interrupt import finalize_interrupt
from .strategy import BaseStreamStrategy

logger = logging.getLogger(__name__)


async def finalize_stream(
    ctx: StreamContext,
    data: Dict,
    tools: List,
    model_instance: Any,
    strategy: BaseStreamStrategy,
    cb,
    fb_callback,
    usage_tracker,
    token_detail_tracker,
) -> AsyncGenerator[Dict, None]:
    """循环成功后的统一收尾

    Args:
        ctx: 流式可变状态
        data: 请求数据
        tools: 工具列表（用于元数据查找）
        model_instance: LLM 模型实例
        strategy: 模式策略
        cb: TokenUsageCallbackHandler
        fb_callback: FallbackDetectionCallback
        usage_tracker: Token 用量追踪器
        token_detail_tracker: Token 详情追踪器
    """
    # 1. 更新用量统计
    update_usage_and_tokens(cb, usage_tracker, token_detail_tracker)

    # 2. LLM fallback 检测
    if fb_callback.fallback_detected:
        fallback_info = fb_callback.get_fallback_info()
        if fallback_info:
            yield {'type': 'model_fallback', 'data': fallback_info}
            try:
                SystemConfig.set_value("default_chat_model", {
                    "provider_id": fallback_info["actual_provider"],
                    "model_name": fallback_info["actual_model"],
                })
            except Exception:
                pass

    # 3. tool_usage 统计
    for tc_info in ctx.tool_calls_map.values():
        tool_name = tc_info.get("name", "unknown")
        token_detail_tracker.track_tool_usage(tool_name)

    # 4. 审批中断收尾（finalize_tool_calls + interrupted 事件 + stream_state 快照）
    if ctx.interrupt_info is not None:
        async for event in finalize_interrupt(ctx, data):
            yield event
        return  # 审批中断时跳过后续 finalize

    # 5. finalize_tool_calls（正常路径）
    for tool_update_event in finalize_tool_calls(
        ctx.all_messages, ctx.tool_calls_map, ctx.tool_args_accumulator,
        session_id=data.get('session_id'),
        message_id=data.get('_assistant_message_id'),
    ):
        yield tool_update_event

    # 6. 深度思考兜底：content 为空时用推理内容作为主内容（仅深度思考模式）
    if (strategy.enable_deep_thinking
            and not ctx.current_message_content.strip()
            and not ctx.interrupt_info
            and ctx.accumulated_reasoning
            and ctx.accumulated_reasoning.get("content", "").strip()):
        reasoning_text = ctx.accumulated_reasoning["content"].strip()
        logger.info(
            f"深度思考兜底: content 为空，将推理内容 ({len(reasoning_text)} 字符) 作为主内容发送"
        )
        yield {"type": "chunk", "content": reasoning_text}
        ctx.current_message_content = reasoning_text

    # 7. strategy.on_loop_success（reasoning 完成事件）
    async for event in strategy.on_loop_success(ctx, data):
        yield event

    # 8. _finalize_stream_response（补发 + 补全检查 + 建议生成）
    async for event in _finalize_stream_response(
        ctx, data, tools, model_instance,
    ):
        yield event


async def _finalize_stream_response(
    ctx: StreamContext,
    data: Dict,
    tools: List,
    model_instance: Any,
) -> AsyncGenerator[Dict, None]:
    """流式响应收尾：补发剩余内容 + 工具结果补发 + 补全检查 + 建议生成

    工具结果补发使用元数据驱动（output_to_chat / raw_content），
    替代原硬编码的 weather_tools / raw_content_tools / knowledge_base_ 前缀。
    """
    # 查找最终 AIMessage
    final_ai_message = None
    for msg in reversed(ctx.all_messages):
        if isinstance(msg, AIMessage) and msg.content and msg.content.strip():
            final_ai_message = msg
            break

    # 补发 final_ai_message 中未流式发送的剩余内容
    if final_ai_message and final_ai_message.content:
        final_content = final_ai_message.content
        if len(final_content) > len(ctx.current_message_content):
            remaining_content = final_content[len(ctx.current_message_content):]
            if remaining_content:
                yield {"type": "chunk", "content": remaining_content}
                ctx.current_message_content = final_content

    # AI 回复过短时，用工具结果补发（元数据驱动）
    if (not final_ai_message
            or not final_ai_message.content
            or len(final_ai_message.content.strip()) < 10) and ctx.tool_calls_map:
        for tool_info in ctx.tool_calls_map.values():
            tool_name = tool_info.get("name", "")
            meta = get_tool_metadata(tool_name, tools)

            # 仅 output_to_chat=True 的工具结果适合作为聊天文本补发
            if not meta.get("output_to_chat"):
                continue

            # raw_content=True 或已摘要的结果不应直接作为聊天文本
            if meta.get("raw_content") or tool_info.get("_summarized"):
                continue

            if (tool_info.get("state") == "output-available"
                    and tool_info.get("result")):
                result = tool_info.get("result")
                if isinstance(result, list):
                    result = str(result)
                if isinstance(result, str) and result not in ctx.current_message_content:
                    yield {"type": "chunk", "content": result}
                    break

    # 补全检查（Agent 模式跳过：Agent 已生成完整回答）
    mode = data.get('mode', 'agent')
    if mode != 'agent' and not ctx.prefer_tool_result and _needs_completion(ctx.current_message_content):
        model = model_instance or get_chat_model()
        prompt = (
            f"用户问题：{data['message']}\n\n"
            f"当前回复（不完整）：{ctx.current_message_content}\n\n"
            "请继续并完整回答上述问题，补充必要的解释或例子，最后给出一句简明结论。"
        )
        try:
            completion = await model.ainvoke([{"role": "user", "content": prompt}])
            extra = getattr(completion, "content", "")
            if extra:
                yield {"type": "chunk", "content": extra}
                ctx.current_message_content += extra
        except Exception:
            pass

    # 建议生成
    try:
        model = model_instance or get_chat_model()
        suggestions_prompt = (
            "你是一个辅助对话的助手。请根据以下用户问题和最终回复，生成4条简洁、相关、可点击的后续问题建议。\n"
            "用JSON数组返回，每个元素是不超过30字的中文字符串，不要包含编号或多余文本。\n\n"
            f"用户问题：{data['message']}\n\n"
            f"最终回复：{ctx.current_message_content}"
        )
        completion = await model.ainvoke([{"role": "user", "content": suggestions_prompt}])
        raw = getattr(completion, "content", "")
        suggestions = extract_suggestions(raw)
        if suggestions:
            yield {'type': 'suggestions', 'data': suggestions}
    except Exception:
        pass
