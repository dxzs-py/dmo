"""
学习工作流节点模型辅助函数

集中处理从 StudyFlowState 提取用户运行时配置并构建 LLM 的逻辑，
避免每个节点重复读取 state 字段。

与深度研究模块的字段语义保持一致：
- provider_id / model_name / temperature / max_tokens / special_params
- enable_deep_thinking 由前端转译为 special_params 中的具体参数（如 DeepSeek 的 thinking），
  后端不再单独处理，与 research_resume_task.py 行为一致。
"""

from typing import Any, Dict, Optional

from Django_xm.apps.ai_engine.services.llm_factory import (
    get_chat_model,
    get_structured_model_with_fallback,
)
from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)


def _extract_model_kwargs(state: Dict[str, Any]) -> Dict[str, Any]:
    """从 StudyFlowState 提取用户运行时模型配置

    仅返回非空字段，避免覆盖 get_chat_model 内部的默认值。
    """
    kwargs: Dict[str, Any] = {}

    provider_id = state.get("provider_id")
    if provider_id:
        kwargs["model_provider"] = provider_id

    model_name = state.get("model_name")
    if model_name:
        kwargs["model_name"] = model_name

    temperature = state.get("temperature")
    if temperature is not None:
        kwargs["temperature"] = temperature

    max_tokens = state.get("max_tokens")
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    special_params = state.get("special_params")
    if special_params:
        kwargs["special_params"] = special_params

    return kwargs


def get_chat_model_from_state(state: Dict[str, Any], **extra: Any):
    """根据 state 中的用户配置构建聊天模型

    调用 get_chat_model，自动具备三层级 fallback（默认 → 辅助 → 降级）能力。
    """
    kwargs = _extract_model_kwargs(state)
    kwargs.update(extra)
    return get_chat_model(**kwargs)


def get_structured_model_from_state(state: Dict[str, Any], schema: Any, **extra: Any):
    """根据 state 中的用户配置构建结构化输出模型

    调用 get_structured_model_with_fallback，自动具备 fallback + 重试能力。
    内部对 DeepSeek 自动使用 JsonModeStructuredModel，避免 thinking + tool_choice 冲突。
    """
    kwargs = _extract_model_kwargs(state)
    kwargs.update(extra)
    # 结构化输出场景默认 streaming=False（与原节点行为保持一致）
    kwargs.setdefault("streaming", False)
    return get_structured_model_with_fallback(schema, **kwargs)
