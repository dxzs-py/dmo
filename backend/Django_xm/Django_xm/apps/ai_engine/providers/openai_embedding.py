"""
OpenAI Embedding Provider

使用 langchain_openai.OpenAIEmbeddings 初始化
文档：https://docs.langchain.com/oss/python/integrations/embeddings/openai
"""
from typing import Any

from langchain_core.embeddings import Embeddings

from Django_xm.apps.ai_engine.config import settings


def get_provider_config() -> dict[str, Any]:
    return {
        "api_key": settings.openai_api_key,
        "base_url": settings.openai_api_base,
        "model": settings.embedding_model,
    }


def create_embedding(
    model: str | None = None,
    batch_size: int = 100,
    dimensions: int | None = None,
    **kwargs: Any,
) -> Embeddings:
    """使用 OpenAI 官方 SDK 创建 Embeddings 实例

    Args:
        model: OpenAI Embedding 模型名（默认从 settings 读取）
        batch_size: 批处理大小
        dimensions: MRL 截断维度（可选）。仅 text-embedding-3-* 系列生效，
            OpenAI API 支持在该系列上截断到指定维度（text-embedding-3-small 最少 1，
            text-embedding-3-large 最少 256）。text-embedding-ada-002 不支持。
        **kwargs: 透传给 OpenAIEmbeddings
    """
    from langchain_openai import OpenAIEmbeddings

    cfg = get_provider_config()
    init_kwargs: dict[str, Any] = {
        "model": model or cfg["model"],
        "api_key": cfg["api_key"],
        "base_url": cfg["base_url"],
        "chunk_size": batch_size,
        "timeout": 60.0,
        "max_retries": 2,
    }
    # OpenAI text-embedding-3-* 系列支持 dimensions 截断
    if dimensions is not None and dimensions > 0:
        init_kwargs["dimensions"] = dimensions
    init_kwargs.update(kwargs)
    return OpenAIEmbeddings(**init_kwargs)
