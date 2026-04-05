"""
Core models public API

统一导出核心模型和LLM相关功能。
此模块作为 core 应用的模型层入口，提供清晰的公共接口。
"""
from .llm_models import (
    get_chat_model,
    get_streaming_model,
    get_structured_output_model,
    get_model_by_preset,
    get_model_string,
    MODEL_CONFIGS,
)

__all__ = [
    'BaseModel',
    'AuditModel',
    'get_chat_model',
    'get_streaming_model',
    'get_structured_output_model',
    'get_model_by_preset',
    'get_model_string',
    'MODEL_CONFIGS',
]


def __getattr__(name):
    if name in ('BaseModel', 'AuditModel'):
        from .base_models import BaseModel, AuditModel
        if name == 'BaseModel':
            return BaseModel
        return AuditModel
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
