"""
百度千帆 Embedding Provider

使用 langchain_community.QianfanEmbeddingsEndpoint 初始化
鉴权：qianfan_ak / qianfan_sk（独立于 OpenAI 兼容接口）
文档：https://python.langchain.ac.cn/docs/integrations/text_embedding/baidu_qianfan_endpoint/

注意：当前 settings 中只有 baidu_qianfan_api_key（作为 qianfan_ak 使用）。
qianfan_sk 通过环境变量 QIANFAN_SK 注入，避免 settings 字段膨胀。
"""

from typing import Any

from langchain_core.embeddings import Embeddings

from Django_xm.apps.ai_engine.config import settings


def get_provider_config() -> dict[str, Any]:
    return {
        "qianfan_ak": settings.baidu_qianfan_api_key,
        "endpoint": settings.baidu_qianfan_api_base,
    }


def create_embedding(
    model: str | None = None,
    dimensions: int | None = None,
    **kwargs: Any,
) -> Embeddings:
    """使用百度千帆官方 SDK 创建 Embeddings 实例

    qianfan_sk 通过环境变量 QIANFAN_SK 或 kwargs 显式传入。

    Args:
        model: 千帆 Embedding 模型名（可选）
        dimensions: MRL 截断维度（可选）。千帆 Embedding 多数模型固定维度，
            此参数当前不传给 QianfanEmbeddingsEndpoint（无对应参数），仅作为
            工厂层接口对齐占位。如果未来千帆支持维度截断，可在此启用。
        **kwargs: 透传给 QianfanEmbeddingsEndpoint
    """
    from langchain_community.embeddings import QianfanEmbeddingsEndpoint

    cfg = get_provider_config()
    init_kwargs: dict[str, Any] = {
        "qianfan_ak": cfg["qianfan_ak"],
    }
    if model:
        init_kwargs["model"] = model
    # 允许外部通过 kwargs 显式覆盖（不放入 None，避免 pydantic 校验）
    if "qianfan_sk" in kwargs:
        init_kwargs["qianfan_sk"] = kwargs.pop("qianfan_sk")
    if "endpoint" in kwargs:
        init_kwargs["endpoint"] = kwargs.pop("endpoint")
    # 千帆暂不支持 dimensions 透传（无对应 API 参数），保留入参仅作接口对齐
    kwargs.pop("dimensions", None)
    init_kwargs.update(kwargs)
    return QianfanEmbeddingsEndpoint(**init_kwargs)
