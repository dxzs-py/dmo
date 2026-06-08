"""
Ollama Chat Provider

使用 langchain_ollama.ChatOllama 初始化（官方推荐包）
文档：https://docs.langchain.com/oss/python/integrations/chat/ollama

支持工具调用、结构化输出、流式输出。
推荐模型：qwen3:8b（中文工具调用强）、deepseek-r1:8b（深度思考）

使用：
    ollama pull qwen3:8b
    ollama serve
"""
from typing import Any, Dict, Optional

from langchain_core.language_models.chat_models import BaseChatModel

from Django_xm.apps.ai_engine.config import settings, get_logger

logger = get_logger(__name__)


def get_provider_config() -> Dict[str, Any]:
    return {
        "base_url": getattr(settings, "ollama_base_url", "http://localhost:11435"),
        "model": getattr(settings, "ollama_model", "qwen3:8b"),
    }


def create_chat_model(
    model: Optional[str] = None,
    temperature: float = 0.7,
    streaming: bool = False,
    **kwargs: Any,
) -> BaseChatModel:
    """使用 Ollama 本地服务创建 Chat 模型

    Args:
        model: Ollama 中已 pull 的模型名
        temperature: 采样温度
        streaming: 是否启用流式
        **kwargs: 透传给 ChatOllama
    """
    from langchain_ollama import ChatOllama

    cfg = get_provider_config()
    return ChatOllama(
        model=model or cfg["model"],
        base_url=cfg["base_url"],
        temperature=temperature,
        streaming=streaming,
        **kwargs,
    )
