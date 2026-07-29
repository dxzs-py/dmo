"""knowledge 模块统一配置入口

收敛对 ai_engine 的直接导入，knowledge 模块内其他文件应从此处导入，
不再直接 from Django_xm.apps.ai_engine import ...

这些 re-export 是 knowledge 模块的稳定门面：当 ai_engine 内部模块拆分
（如 llm_factory.py 拆分为 llm_cache/llm_fallback/llm_factory）时，
knowledge 模块的导入路径不需要同步修改，降低耦合。
"""

# 配置对象
from Django_xm.apps.ai_engine.config import settings  # noqa: F401

# 系统配置模型
from Django_xm.apps.ai_engine.models import SystemConfig  # noqa: F401

# Embedding 工厂
from Django_xm.apps.ai_engine.services.embedding_factory import (  # noqa: F401
    FallbackEmbedding,
    detect_embedding_dimension,
    get_embeddings_with_fallback,
    get_system_embedding_provider,
)

# LLM 工厂
from Django_xm.apps.ai_engine.services.llm_factory import (  # noqa: F401
    get_chat_model,
    get_model_string,
)
