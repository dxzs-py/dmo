from typing import Any, Dict

from Django_xm.apps.ai_engine.config import get_logger

logger = get_logger(__name__)

_patch_applied: bool = False


# 延迟导入，避免循环依赖
def _get_registry_config() -> Dict[str, Any]:
    from Django_xm.apps.ai_engine.services.registry_service import get_provider_config
    return get_provider_config("deepseek")


def get_provider_config() -> Dict[str, Any]:
    return _get_registry_config().copy()


def apply_reasoning_patch_if_needed() -> None:
    """懒加载应用 reasoning_content 补丁，仅在首次调用时执行"""
    global _patch_applied
    if _patch_applied:
        return
    apply_reasoning_patch()


def apply_reasoning_patch() -> None:
    global _patch_applied
    if _patch_applied:
        return

    try:
        from langchain_openai.chat_models.base import (
            _convert_delta_to_message_chunk as _original_chunk_convert,
            _convert_message_to_dict as _original_msg_convert,
            AIMessageChunk,
            AIMessage,
        )

        _chunk_ref = _original_chunk_convert
        _msg_ref = _original_msg_convert

        def _convert_delta_with_reasoning(_dict, default_class):
            result = _chunk_ref(_dict, default_class)
            if isinstance(result, AIMessageChunk):
                rc = _dict.get("reasoning_content")
                if rc and isinstance(rc, str):
                    existing = getattr(result, "additional_kwargs", {}) or {}
                    existing["reasoning_content"] = rc
                    result.additional_kwargs = existing
            return result

        def _convert_msg_with_reasoning(message, api="chat/completions"):
            result = _msg_ref(message, api=api)
            if isinstance(message, AIMessage):
                rc = getattr(message, "additional_kwargs", {}).get("reasoning_content")
                if rc and isinstance(rc, str) and rc.strip():
                    result["reasoning_content"] = rc
            return result

        import langchain_openai.chat_models.base as _base_module
        _base_module._convert_delta_to_message_chunk = _convert_delta_with_reasoning
        _base_module._convert_message_to_dict = _convert_msg_with_reasoning
        _patch_applied = True
        logger.info("DeepSeek reasoning_content patch 已应用（langchain_openai 流式修复）")
    except Exception as e:
        logger.warning(f"DeepSeek reasoning_content patch 应用失败: {e}")


def is_thinking_enabled(special_params: Dict[str, Any], provider_id: str = "") -> bool:
    """通用判断深度思考是否启用，兼容 DeepSeek/Ollama/Anthropic 等多 Provider。

    - DeepSeek: thinking={"type": "enabled"} 或 reasoning_effort 存在
    - Ollama: thinking=True 或 reasoning=True
    - Anthropic: thinking={"type": "enabled", "budget_tokens": N}
    """
    if not special_params:
        return False
    # 通用检查：thinking 参数
    thinking_val = special_params.get("thinking")
    if thinking_val:
        # DeepSeek/Anthropic 格式: {"type": "enabled", ...}
        if isinstance(thinking_val, dict) and thinking_val.get("type") == "enabled":
            return True
        # Ollama 格式: True
        if isinstance(thinking_val, bool) and thinking_val:
            return True
    # DeepSeek 特有：reasoning_effort 存在时 thinking 自动启用
    if "reasoning_effort" in special_params:
        return True
    # Ollama 特有：reasoning 参数
    if special_params.get("reasoning"):
        return True
    return False
