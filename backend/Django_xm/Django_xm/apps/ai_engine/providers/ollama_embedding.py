"""
Ollama Embedding Provider

使用 langchain_ollama.OllamaEmbeddings 初始化（官方推荐包）
文档：https://reference.langchain.com/python/langchain-ollama/

推荐模型（Ollama 模型库）：
- bge-m3                 (1024 维，中英多语言最强，王者选择)
- nomic-embed-text       (768 维，英文为主，中文也可用)
- mxbai-embed-large      (1024 维，纯英文最佳)
- snowflake-arctic-embed (1024 维，多语言)
- all-minilm             (384 维，最快最小，质量一般)

下载：
    ollama pull bge-m3
    ollama pull nomic-embed-text
"""
from typing import Any

from langchain_core.embeddings import Embeddings

from Django_xm.apps.ai_engine.config import settings


def get_provider_config() -> dict[str, Any]:
    return {
        "base_url": getattr(settings, "ollama_base_url", "http://localhost:11435"),
        "model": getattr(settings, "ollama_embedding_model", "bge-m3"),
    }


def create_embedding(
    model: str | None = None,
    dimensions: int | None = None,
    **kwargs: Any,
) -> Embeddings:
    """使用 Ollama 本地服务创建 Embeddings 实例

    Args:
        model: Ollama 中已 pull 的模型名（默认从 settings 读取）
        dimensions: MRL 截断维度（可选）。仅 Matryoshka 训练模型（nomic-embed-text、
            qwen3-embedding、embeddinggemma 等）生效，Ollama API 会自动截断输出向量
            到此维度并重新归一化。非 MRL 模型传此参数会被 Ollama 忽略。
        **kwargs: 透传给 OllamaEmbeddings
    """
    from langchain_ollama import OllamaEmbeddings

    cfg = get_provider_config()
    init_kwargs: dict[str, Any] = {
        "model": model or cfg["model"],
        "base_url": cfg["base_url"],
    }
    # OllamaEmbeddings 原生支持 dimensions（MRL 截断）
    if dimensions is not None and dimensions > 0:
        init_kwargs["dimensions"] = dimensions
    init_kwargs.update(kwargs)
    return OllamaEmbeddings(**init_kwargs)
