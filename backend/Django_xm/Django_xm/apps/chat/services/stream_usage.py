"""用量与 Token 统计辅助（Task 15.2 从 stream_helpers.py 拆分）。

集中处理流式聊天中的用量（usage）与 Token 明细追踪：
- ``build_context_info``：构建上下文信息（用量 + token 明细 + 响应时间）
- ``update_usage_and_tokens``：从 CallbackManager 更新用量与 token 明细
- ``sync_usage_from_messages``：从累积消息的 response_metadata 同步用量

依赖方向：本模块仅依赖 ``langchain_core`` 与标准库，不依赖其他 stream_* 子模块。
"""

import time

from langchain_core.messages import AIMessage


def build_context_info(usage_tracker, token_detail_tracker, stream_start_time=None):
    context_info = usage_tracker.get_usage_info()
    token_summary = token_detail_tracker.get_summary()
    context_info["tokens"] = token_summary["tokens"]
    context_info["tokenDetail"] = token_detail_tracker.get_token_detail()
    context_info["model"] = usage_tracker.model_id
    context_info["total_tokens"] = usage_tracker.get_total_tokens()
    if stream_start_time is not None:
        context_info["response_time"] = round(time.time() - stream_start_time, 2)
    return context_info


def update_usage_and_tokens(cb, usage_tracker, token_detail_tracker=None):
    usage_tracker.add_input_tokens(cb.prompt_tokens)
    usage_tracker.add_output_tokens(cb.completion_tokens)
    if token_detail_tracker:
        token_detail_tracker.update_from_metadata(
            {
                "usage_metadata": {
                    "input_tokens": cb.prompt_tokens,
                    "output_tokens": cb.completion_tokens,
                }
            }
        )
        token_detail_tracker.finish_record()


def sync_usage_from_messages(all_messages, usage_tracker, token_detail_tracker=None):
    seen_ids = set()
    for msg in reversed(all_messages):
        if not isinstance(msg, AIMessage):
            continue
        msg_id = getattr(msg, "id", None)
        if msg_id and msg_id in seen_ids:
            continue
        if msg_id:
            seen_ids.add(msg_id)
        resp_meta = getattr(msg, "response_metadata", {}) or {}
        token_usage = resp_meta.get("token_usage", {})
        if token_usage:
            usage_tracker.add_input_tokens(token_usage.get("prompt_tokens", 0))
            usage_tracker.add_output_tokens(token_usage.get("completion_tokens", 0))
            if token_detail_tracker:
                token_detail_tracker.update_from_metadata(
                    {
                        "usage_metadata": {
                            "input_tokens": token_usage.get("prompt_tokens", 0),
                            "output_tokens": token_usage.get("completion_tokens", 0),
                        }
                    }
                )
        usage_meta = resp_meta.get("usage_metadata", {})
        if usage_meta:
            usage_tracker.update_from_metadata({"usage_metadata": usage_meta})
            if token_detail_tracker:
                token_detail_tracker.update_from_metadata({"usage_metadata": usage_meta})
