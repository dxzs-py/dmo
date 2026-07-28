from typing import Any, Dict

from .anthropic import get_provider_config as get_anthropic_config
from .deepseek import apply_reasoning_patch, apply_reasoning_patch_if_needed, is_thinking_enabled
from .deepseek import get_provider_config as get_deepseek_config
from .groq import get_provider_config as get_groq_config
from .groq import is_groq_model, patch_groq_model
from .local_embedding import create_embedding as create_local_embedding
from .ollama import create_chat_model as create_ollama_chat_model
from .ollama import get_provider_config as get_ollama_config
from .ollama_embedding import create_embedding as create_ollama_embedding
from .openai import get_provider_config as get_openai_config

# Embedding providers
from .openai_embedding import create_embedding as create_openai_embedding
from .qianfan import get_provider_config as get_qianfan_config
from .qianfan_embedding import create_embedding as create_qianfan_embedding


def get_all_provider_configs() -> dict[str, dict[str, Any]]:
    """每次调用都从数据库读取最新配置（通过 registry_service 缓存机制）"""
    from Django_xm.apps.ai_engine.services.registry_service import get_model_registry
    return {k: v.copy() for k, v in get_model_registry().items()}


# 兼容旧代码：PROVIDER_REGISTRY 改为延迟加载代理
# 不再使用启动时快照，避免 Admin 修改后数据陈旧
class _ProviderRegistryProxy:
    """代理对象，让 PROVIDER_REGISTRY[key] 和 key in PROVIDER_REGISTRY 仍可用，
    但每次访问都从数据库读取最新数据。使用延迟导入避免循环依赖。"""

    def _get_registry(self):
        from Django_xm.apps.ai_engine.services.registry_service import get_model_registry
        return get_model_registry()

    def __getitem__(self, key):
        return self._get_registry()[key].copy()

    def __contains__(self, key):
        return key in self._get_registry()

    def __iter__(self):
        return iter(self._get_registry())

    def items(self):
        return self._get_registry().items()

    def keys(self):
        return self._get_registry().keys()

    def values(self):
        return self._get_registry().values()

    def get(self, key, default=None):
        return self._get_registry().get(key, default)

    def __bool__(self):
        return bool(self._get_registry())


PROVIDER_REGISTRY = _ProviderRegistryProxy()


__all__ = [
    "PROVIDER_REGISTRY",
    "get_all_provider_configs",
    "get_openai_config",
    "get_deepseek_config",
    "get_anthropic_config",
    "get_groq_config",
    "get_qianfan_config",
    "get_ollama_config",
    "apply_reasoning_patch",
    "apply_reasoning_patch_if_needed",
    "is_thinking_enabled",
    "patch_groq_model",
    "is_groq_model",
    # Embedding providers
    "create_openai_embedding",
    "create_qianfan_embedding",
    "create_local_embedding",
    "create_ollama_embedding",
    # Chat providers
    "create_ollama_chat_model",
]
