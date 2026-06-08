"""knowledge 模块统一配置入口

收敛对 ai_engine 的直接导入，knowledge 模块内其他文件应从此处导入，
不再直接 from Django_xm.apps.ai_engine import ...
"""

# 配置对象
from Django_xm.apps.ai_engine.config import settings  # noqa: F401

# LLM 工厂
from Django_xm.apps.ai_engine.services.llm_factory import (  # noqa: F401
    get_chat_model,
    get_model_string,
)

# Embedding 工厂
from Django_xm.apps.ai_engine.services.embedding_factory import (  # noqa: F401
    FallbackEmbedding,
    get_embeddings_with_fallback,
    detect_embedding_dimension,
    get_system_embedding_provider,
)

# 系统配置模型
from Django_xm.apps.ai_engine.models import SystemConfig  # noqa: F401
