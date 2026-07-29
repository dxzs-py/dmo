"""
Model instance cache - thread-safe LRU cache for LLM model instances.

Eliminates duplicated cache logic across model factory functions.
"""

import logging
import threading
from collections import OrderedDict

from langchain_core.language_models.chat_models import BaseChatModel

logger = logging.getLogger(__name__)

_model_instance_cache: OrderedDict[str, BaseChatModel] = OrderedDict()
_model_cache_lock = threading.Lock()


def _get_cache_maxsize():
    from django.conf import settings

    return getattr(settings, "AI_MODEL_CACHE_MAXSIZE", 32)


def make_cache_key(
    model_name: str,
    provider: str,
    temperature: float,
    streaming: bool,
    max_tokens: int | None,
    special_suffix: str = "",
    api_key: str | None = None,
    base_url: str | None = None,
    max_retries: int | None = None,
) -> str:
    """生成模型缓存 key，包含认证信息以区分不同 API 配置的同名模型"""
    key = f"{provider}:{model_name}:t{temperature}:s{streaming}:mt{max_tokens}"
    # max_retries 影响模型行为（快速失败 vs 重试），需纳入缓存 key
    if max_retries is not None:
        key += f":mr{max_retries}"
    # 包含 api_key 哈希和 base_url 以区分不同提供商配置的同名模型
    if api_key:
        key += f":ak{hash(api_key)}"
    if base_url:
        key += f":bu{base_url}"
    if special_suffix:
        key += special_suffix
    return key


def get_cached_model(cache_key: str) -> BaseChatModel | None:
    """Thread-safe LRU cache lookup. Returns None on miss."""
    with _model_cache_lock:
        if cache_key in _model_instance_cache:
            _model_instance_cache.move_to_end(cache_key)
            logger.debug(f"复用已缓存的模型实例: {cache_key}")
            return _model_instance_cache[cache_key]
    return None


def set_cached_model(cache_key: str, model: BaseChatModel) -> None:
    """Thread-safe LRU cache insert with eviction."""
    with _model_cache_lock:
        _model_instance_cache[cache_key] = model
        _model_instance_cache.move_to_end(cache_key)
        if len(_model_instance_cache) > _get_cache_maxsize():
            evicted_key, _ = _model_instance_cache.popitem(last=False)
            logger.debug(f"模型缓存已满，LRU淘汰: {evicted_key}")
