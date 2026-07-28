"""
本地 HuggingFace Embedding Provider

使用 langchain_community.HuggingFaceBgeEmbeddings 初始化
用途：作为完全本地化的 embedding 兜底方案（不消耗任何在线 API 额度）
模型默认：BAAI/bge-small-zh-v1.5（中文友好，384 维，体积小）

注意：
- DeepSeek 不提供 OpenAI 兼容 embedding 接口（仅有 deepseek-chat/deepseek-reasoner）
- 百度千帆 embedding 走独立 QianfanEmbeddingsEndpoint
- 本地 embedding 作为最终兜底，确保 RAG 不完全中断
"""
from typing import Any

from langchain_core.embeddings import Embeddings

from Django_xm.apps.ai_engine.config import settings


def create_embedding(
    model_name: str | None = None,
    dimensions: int | None = None,
    **kwargs: Any,
) -> Embeddings:
    """使用 HuggingFace 本地模型创建 Embeddings 实例

    Args:
        model_name: HuggingFace 模型名（默认从 settings 读取）
        dimensions: MRL 截断维度（可选）。HuggingFace BGE 多数模型为固定维度，
            此参数当前不传给 HuggingFaceBgeEmbeddings（无对应参数），仅作
            工厂层接口对齐占位。
        **kwargs: 透传给 HuggingFaceBgeEmbeddings
    """
    from langchain_community.embeddings import HuggingFaceBgeEmbeddings

    model_name = model_name or getattr(
        settings, "local_embedding_model", "BAAI/bge-small-zh-v1.5"
    )
    # 本地 BGE 不支持 dimensions 透传，保留入参仅作接口对齐
    kwargs.pop("dimensions", None)
    return HuggingFaceBgeEmbeddings(
        model_name=model_name,
        **kwargs,
    )
