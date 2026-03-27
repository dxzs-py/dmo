"""
模型封装模块
提供统一的 LLM 模型接口，支持 OpenAI 等多种提供商

使用 LangChain 1.0.3 的标准接口封装模型
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
    """
    获取配置好的聊天模型实例

    这是一个工厂函数，根据配置创建 LangChain 的 ChatModel 实例。
    默认使用 OpenAI 的模型，支持流式输出和自定义参数。
    """
    model_name = model_name or settings.openai_model
    temperature = temperature if temperature is not None else settings.openai_temperature
    streaming = streaming if streaming is not None else settings.openai_streaming

    model_config: Dict[str, Any] = {
        "model": model_name,
        "temperature": temperature,
        "streaming": streaming,
        "api_key": settings.openai_api_key,
        "base_url": settings.openai_api_base,
    }

    if max_tokens is not None:
        model_config["max_tokens"] = max_tokens
    elif settings.openai_max_tokens is not None:
        model_config["max_tokens"] = settings.openai_max_tokens

    model_config.update(kwargs)

    logger.info(
        f"🤖 创建聊天模型: {model_name} "
        f"(temperature={temperature}, streaming={streaming})"
    )

    try:
        model = ChatOpenAI(**model_config)
        logger.debug(f"✅ 模型创建成功: {model_name}")
        return model
    except Exception as e:
        logger.error(f"❌ 模型创建失败: {e}")
        raise


def get_streaming_model(
    model_name: Optional[str] = None,
    temperature: Optional[float] = None,
    **kwargs: Any,
) -> BaseChatModel:
    """
    获取启用流式输出的聊天模型
    """
    return get_chat_model(
        model_name=model_name,
        temperature=temperature,
        streaming=True,
        **kwargs,
    )


def get_structured_output_model(
    model_name: Optional[str] = None,
    temperature: float = 0.0,
    **kwargs: Any,
) -> BaseChatModel:
    """
    获取用于结构化输出的聊天模型
    """
    return get_chat_model(
        model_name=model_name,
        temperature=temperature,
        streaming=False,
        **kwargs,
    )


MODEL_CONFIGS = {
    "default": {
        "model_name": "gpt-4o",
        "temperature": 0.7,
        "description": "默认模型，平衡性能和成本",
    },
    "fast": {
        "model_name": "gpt-4o-mini",
        "temperature": 0.7,
        "description": "快速模型，适合简单任务",
    },
    "precise": {
        "model_name": "gpt-4o",
        "temperature": 0.3,
        "description": "精确模型，适合需要准确性的任务",
    },
    "creative": {
        "model_name": "gpt-4o",
        "temperature": 1.0,
        "description": "创意模型，适合需要创造性的任务",
    },
}


def get_model_by_preset(preset: str = "default", **kwargs: Any) -> BaseChatModel:
    """
    根据预设配置获取模型
    """
    if preset not in MODEL_CONFIGS:
        available = ", ".join(MODEL_CONFIGS.keys())
        raise ValueError(f"未知的预设: {preset}. 可用预设: {available}")

    config = MODEL_CONFIGS[preset].copy()
    config.pop("description", None)
    config.update(kwargs)

    logger.info(f"📋 使用预设模型配置: {preset}")
    return get_chat_model(**config)


def get_model_string(
    model_name: Optional[str] = None,
    provider: str = "openai",
) -> str:
    """
    获取模型标识符字符串
    """
    model_name = model_name or settings.openai_model
    model_string = f"{provider}:{model_name}"

    logger.debug(f"🔤 生成模型标识符: {model_string}")
    return model_string
