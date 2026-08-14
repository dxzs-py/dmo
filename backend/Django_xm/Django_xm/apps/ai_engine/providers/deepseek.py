from typing import Any

from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)

_patch_applied: bool = False


# 延迟导入，避免循环依赖
def _get_registry_config() -> dict[str, Any]:
    from Django_xm.apps.ai_engine.services.registry_service import get_provider_config

    return get_provider_config("deepseek")


def get_provider_config() -> dict[str, Any]:
    return _get_registry_config().copy()


def apply_reasoning_patch_if_needed() -> None:
    """懒加载应用 reasoning_content 补丁，仅在首次调用时执行"""
    if _patch_applied:
        return
    apply_reasoning_patch()


def apply_reasoning_patch() -> None:
    global _patch_applied  # noqa: PLW0603 - 模块级补丁状态标记惰性初始化
    if _patch_applied:
        return

    try:
        from langchain_openai.chat_models.base import (
            AIMessage,
            AIMessageChunk,
        )
        from langchain_openai.chat_models.base import (
            _convert_delta_to_message_chunk as _original_chunk_convert,
        )
        from langchain_openai.chat_models.base import (
            _convert_message_to_dict as _original_msg_convert,
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

