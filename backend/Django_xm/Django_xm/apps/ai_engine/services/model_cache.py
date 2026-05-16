"""
Model instance cache - thread-safe LRU cache for LLM model instances.

Eliminates duplicated cache logic across model factory functions.
"""

import threading
from collections import OrderedDict
from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel

import logging

logger = logging.getLogger(__name__)

_model_instance_cache: OrderedDict[str, BaseChatModel] = OrderedDict()
_MODEL_CACHE_MAXSIZE = 32
_model_cache_lock: Optional[threading.Lock] = None


def _get_cache_lock():
    global _model_cache_lock
    if _model_cache_lock is None:
        _model_cache_lock = threading.Lock()
    return _model_cache_lock


def make_cache_key(
    model_name: str,
    provider: str,
    temperature: float,
    streaming: bool,
    max_tokens: Optional[int],
    special_suffix: str = "",
) -> str:
    key = f"{provider}:{model_name}:t{temperature}:s{streaming}:mt{max_tokens}"
    if special_suffix:
        key += special_suffix
    return key


def get_cached_model(cache_key: str) -> Optional[BaseChatModel]:
    """Thread-safe LRU cache lookup. Returns None on miss."""
    with _get_cache_lock():
        if cache_key in _model_instance_cache:
            _model_instance_cache.move_to_end(cache_key)
            logger.debug(f"复用已缓存的模型实例: {cache_key}")
            return _model_instance_cache[cache_key]
    return None


def set_cached_model(cache_key: str, model: BaseChatModel) -> None:
    """Thread-safe LRU cache insert with eviction."""
    with _get_cache_lock():
        _model_instance_cache[cache_key] = model
        _model_instance_cache.move_to_end(cache_key)
        if len(_model_instance_cache) > _MODEL_CACHE_MAXSIZE:
            evicted_key, _ = _model_instance_cache.popitem(last=False)
            logger.debug(f"模型缓存已满，LRU淘汰: {evicted_key}")
