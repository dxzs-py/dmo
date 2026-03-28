"""
模型封装模块
提供统一的 LLM 模型接口，支持 OpenAI 等多种提供商
"""

from typing import Optional, Dict, Any
from langchain_openai import ChatOpenAI
from langchain_core.language_models.chat_models import BaseChatModel

from .config import settings, get_logger

logger = get_logger(__name__)


def get_chat_model(
    model_name: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    streaming: Optional[bool] = None,
    **kwargs: Any,
) -> BaseChatModel:
    model_name = model_name or settings.openai_model
    temperature = temperature if temperature is not None else settings.openai_temperature
    streaming = streaming if streaming is not None else settings.openai_streaming

    model_config: Dict[str, Any] = {
        "model": model_name,
        "temperature": temperature,
        "streaming": streaming,
        "api_key": settings.openai_api_key,
        "base_url": settings.openai_api_base,
        "timeout": 120.0,
        "max_retries": 3,
    }

    if max_tokens is not None:
        model_config["max_tokens"] = max_tokens
    elif settings.openai_max_tokens is not None:
        model_config["max_tokens"] = settings.openai_max_tokens

    model_config.update(kwargs)

    logger.info(f"创建聊天模型: {model_name} (temperature={temperature}, streaming={streaming})")

    try:
        model = ChatOpenAI(**model_config)
        return model
    except Exception as e:
        logger.error(f"模型创建失败: {e}")
        raise


def get_streaming_model(
    model_name: Optional[str] = None,
    temperature: Optional[float] = None,
    **kwargs: Any,
) -> BaseChatModel:
    return get_chat_model(model_name=model_name, temperature=temperature, streaming=True, **kwargs)


def get_structured_output_model(
    model_name: Optional[str] = None,
    temperature: float = 0.0,
    **kwargs: Any,
) -> BaseChatModel:
    return get_chat_model(model_name=model_name, temperature=temperature, streaming=False, **kwargs)


MODEL_CONFIGS = {
    "default": {"model_name": "gpt-4o", "temperature": 0.7, "description": "默认模型"},
    "fast": {"model_name": "gpt-4o-mini", "temperature": 0.7, "description": "快速模型"},
    "precise": {"model_name": "gpt-4o", "temperature": 0.3, "description": "精确模型"},
    "creative": {"model_name": "gpt-4o", "temperature": 1.0, "description": "创意模型"},
}


def get_model_by_preset(preset: str = "default", **kwargs: Any) -> BaseChatModel:
    if preset not in MODEL_CONFIGS:
        available = ", ".join(MODEL_CONFIGS.keys())
        raise ValueError(f"未知的预设: {preset}. 可用预设: {available}")

    config = MODEL_CONFIGS[preset].copy()
    config.pop("description", None)
    config.update(kwargs)

    logger.info(f"使用预设模型配置: {preset}")
    return get_chat_model(**config)


def get_model_string(model_name: Optional[str] = None, provider: str = "openai") -> str:
    model_name = model_name or settings.openai_model
    model_string = f"{provider}:{model_name}"
    logger.debug(f"生成模型标识符: {model_string}")
    return model_string