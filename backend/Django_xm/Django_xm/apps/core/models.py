"""
注意：此文件已重命名为 llm_models.py
保留此文件仅用于向后兼容
请使用 from .llm_models import ... 代替
"""
import warnings

warnings.warn(
    "core.models 模块已重命名为 core.llm_models，"
    "请更新您的导入语句",
    DeprecationWarning,
    stacklevel=2
)

from .llm_models import (
    get_chat_model,
    get_streaming_model,
    get_structured_output_model,
    get_model_by_preset,
    get_model_string,
    MODEL_CONFIGS,
)

__all__ = [
    'get_chat_model',
    'get_streaming_model',
    'get_structured_output_model',
    'get_model_by_preset',
    'get_model_string',
    'MODEL_CONFIGS',
]
