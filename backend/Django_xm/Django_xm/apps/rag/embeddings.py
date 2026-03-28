"""
Embeddings 模块

提供统一的 Embedding 模型接口，用于将文本转换为向量。
"""

from typing import Optional
from langchain_openai import OpenAIEmbeddings
from langchain_core.embeddings import Embeddings

import logging

logger = logging.getLogger(__name__)


def get_openai_api_key() -> Optional[str]:
    try:
        from Django_xm.apps.core.config import settings
        return getattr(settings, 'openai_api_key', None)
    except ImportError:
        import os
        return os.environ.get("OPENAI_API_KEY")


def get_openai_api_base() -> str:
    try:
        from Django_xm.apps.core.config import settings
        return getattr(settings, 'openai_api_base', "https://api.openai.com/v1")
    except ImportError:
        import os
        return os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")


def get_embedding_model() -> str:
    try:
        from Django_xm.apps.core.config import settings
        return getattr(settings, 'embedding_model', "text-embedding-3-small")
    except ImportError:
        import os
        return os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")


def get_embedding_batch_size() -> int:
    try:
        from Django_xm.apps.core.config import settings
        return getattr(settings, 'embedding_batch_size', 10)
    except ImportError:
        import os
        return int(os.environ.get("EMBEDDING_BATCH_SIZE", "10"))


def get_embeddings(
    model: Optional[str] = None,
    batch_size: Optional[int] = None,
    **kwargs,
) -> Embeddings:
    model = model or get_embedding_model()
    batch_size = batch_size or get_embedding_batch_size()

    logger.info(f"🔢 创建 Embedding 模型: {model}, batch_size={batch_size}")

    try:
        embeddings = OpenAIEmbeddings(
            model=model,
            api_key=get_openai_api_key(),
            base_url=get_openai_api_base(),
            chunk_size=batch_size,
            timeout=60.0,
            max_retries=3,
            **kwargs,
        )

        logger.debug("✅ Embedding 模型创建成功")
        return embeddings

    except Exception as e:
        logger.error(f"❌ Embedding 模型创建失败: {e}")
        raise