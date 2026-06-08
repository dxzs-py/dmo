"""
LLM 模型封装模块
提供统一的 LLM 模型接口，支持 OpenAI 等多种提供商

使用 LangChain v1.2+ 的 init_chat_model 统一模型初始化，
替代手动 ChatOpenAI 实例化，实现单一真相源。

改进：
1. 集成 InMemoryRateLimiter 防止 API 过载
2. 支持多模型提供商（OpenAI/Anthropic 等）
3. 提供商配置拆分至 providers 模块，单一真相源

参考：
- https://docs.langchain.com/oss/python/langchain/models
- https://reference.langchain.com/python/langchain/chat_models/#init_chat_model
"""

from typing import Optional, Dict, Any, Union, List, Tuple, AsyncIterator, Iterator, Callable
import threading
import time
import warnings
from functools import wraps

# 抑制 OpenAI SDK 与 Pydantic v2 的兼容性警告：
# OpenAI SDK 的 ParsedChatCompletionMessage.parsed 字段类型为 Optional[ContentType]，
# 解析后实际为 Optional[None]，但 with_structured_output 会将 Pydantic BaseModel 实例
# 填入 additional_kwargs["parsed"]。LangChain tracer 在 on_llm_end 回调中调用
# LLMResult.model_dump() 时，Pydantic 发现类型不匹配，触发警告。
# 警告消息是多行格式：第一行 "Pydantic serializer warnings:"，第二行才是具体类型，
# 因此用 [\s\S]* 匹配跨行内容（.* 不匹配换行符）。
warnings.filterwarnings(
    "ignore",
    message=r"Pydantic serializer warnings[\s\S]*PydanticSerializationUnexpectedValue",
    category=UserWarning,
)

from django.conf import settings as django_settings
from langchain.chat_models import init_chat_model
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.outputs import ChatGenerationChunk, ChatResult

from ..config import settings, get_logger, get_model_presets, HELPER_MODEL_PRIORITY
from .model_cache import make_cache_key, get_cached_model, set_cached_model
from .registry_service import get_model_registry, get_provider_config, is_provider_valid, is_provider_available as registry_is_provider_available
from ..providers import (
    PROVIDER_REGISTRY,
    apply_reasoning_patch_if_needed,
    is_thinking_enabled,
    patch_groq_model,
)

logger = get_logger(__name__)


def model_supports_capability(provider_id: str, model_name: str, capability: str) -> bool:
    """检查模型是否支持指定能力"""
    provider_cfg = get_provider_config(provider_id)
    for model_cfg in provider_cfg.get("models", []):
        if isinstance(model_cfg, dict) and model_cfg.get("name") == model_name:
            return capability in model_cfg.get("capabilities", [])
        elif isinstance(model_cfg, str) and model_cfg == model_name:
            return False  # 旧格式兼容
    return False


_rate_limiter_lock = threading.Lock()
_rate_limiter: Optional[Any] = None
_llm_cache_lock = threading.Lock()
_llm_cache: Optional[Any] = None


def get_llm_cache() -> Any:
    global _llm_cache
    if _llm_cache is not None:
        return _llm_cache

    with _llm_cache_lock:
        if _llm_cache is not None:
            return _llm_cache

        try:
            from langchain_core.caches import InMemoryCache

            _llm_cache = InMemoryCache()
            logger.info("LLM InMemoryCache 已创建")
            return _llm_cache
        except ImportError:
            logger.warning("langchain_core.caches.InMemoryCache 不可用")
            return None
        except Exception as e:
            logger.warning(f"LLM Cache 创建失败: {e}")
            return None


def setup_llm_cache() -> None:
    try:
        from langchain_core.globals import set_llm_cache

        cache = get_llm_cache()
        if cache is not None:
            set_llm_cache(cache)
            logger.info("全局 LLM Cache 已设置 (via langchain_core.globals)")
    except ImportError:
        try:
            import langchain_core
            cache = get_llm_cache()
            if cache is not None:
                langchain_core.llm_cache = cache
                logger.info("全局 LLM Cache 已设置 (via langchain_core.llm_cache, 兼容模式)")
        except Exception as e:
            logger.warning(f"设置全局 LLM Cache 失败: {e}")
    except Exception as e:
        logger.warning(f"设置全局 LLM Cache 失败: {e}")


def setup_semantic_cache(
    redis_url: Optional[str] = None,
    embedding_model: Optional[str] = None,
) -> None:
    try:
        from langchain_community.cache import RedisSemanticCache
        from langchain_core.globals import set_llm_cache
        from Django_xm.apps.knowledge.services.embedding_service import get_embeddings

        redis = redis_url or getattr(settings, "redis_url", "redis://127.0.0.1:6379/5")
        model_name = embedding_model or getattr(settings, "embedding_model", "text-embedding-3-small")
        embeddings = get_embeddings(model=model_name, use_cache=False)

        semantic_cache = RedisSemanticCache(
            redis_url=redis,
            embedding=embeddings,
        )
        set_llm_cache(semantic_cache)
        logger.info(f"语义缓存已设置 (redis={redis}, model={model_name})")
    except ImportError:
        try:
            from langchain_community.cache import RedisSemanticCache
            import langchain_core
            from Django_xm.apps.knowledge.services.embedding_service import get_embeddings

            redis = redis_url or getattr(settings, "redis_url", "redis://127.0.0.1:6379/5")
            model_name = embedding_model or getattr(settings, "embedding_model", "text-embedding-3-small")
            embeddings = get_embeddings(model=model_name, use_cache=False)

            langchain_core.llm_cache = RedisSemanticCache(
                redis_url=redis,
                embedding=embeddings,
            )
            logger.info(f"语义缓存已设置 (兼容模式, redis={redis}, model={model_name})")
        except ImportError:
            logger.warning(
                "langchain_community.cache.RedisSemanticCache 不可用，"
                "请安装: pip install langchain-community redis"
            )
        except Exception as e:
            logger.warning(f"设置语义缓存失败: {e}，回退到 InMemoryCache")
            setup_llm_cache()


def get_rate_limiter() -> Any:
    global _rate_limiter
    if _rate_limiter is not None:
        return _rate_limiter

    with _rate_limiter_lock:
        if _rate_limiter is not None:
            return _rate_limiter

        try:
            from langchain_core.rate_limiters import InMemoryRateLimiter

            requests_per_minute = getattr(settings, "rate_limit_rpm", 60) or 60
            requests_per_second = getattr(settings, "rate_limit_rps", 1) or 1
            max_concurrency = getattr(settings, "rate_limit_max_concurrency", 10) or 10

            _rate_limiter = InMemoryRateLimiter(
                requests_per_second=requests_per_second,
                check_every_n_seconds=0.1,
                max_bucket_size=requests_per_minute,
            )

            logger.info(
                f"速率限制器已创建: {requests_per_second} req/s, "
                f"bucket={requests_per_minute}, max_concurrency={max_concurrency}"
            )
            return _rate_limiter
        except ImportError:
            logger.warning(
                "langchain_core.rate_limiters.InMemoryRateLimiter 不可用，"
                "请升级 langchain-core>=0.3.0"
            )
            return None
        except Exception as e:
            logger.warning(f"速率限制器创建失败: {e}，将不使用速率限制")
            return None


def _cached_model_creation(
    cache_key: str,
    use_cache: bool,
    creation_func: Callable[[], BaseChatModel],
    error_context: str = "",
) -> BaseChatModel:
    """统一的模型缓存逻辑封装

    将缓存查找 → 模型创建 → 缓存写入的通用流程抽取为单一函数，
    消除 _create_single_chat_model 和 get_chat_model_by_provider 中的重复代码。

    Args:
        cache_key: 缓存键
        use_cache: 是否启用缓存
        creation_func: 模型创建函数，返回 BaseChatModel 实例
        error_context: 错误日志的上下文信息（如 provider_id）

    Returns:
        缓存的或新创建的模型实例
    """
    if use_cache:
        cached = get_cached_model(cache_key)
        if cached is not None:
            return cached

    try:
        model = creation_func()

        if use_cache:
            set_cached_model(cache_key, model)

        return model
    except Exception as e:
        ctx = f" ({error_context})" if error_context else ""
        logger.error(f"模型创建失败{ctx}: {e}")
        raise


def _create_single_chat_model(
    model_name: Optional[str] = None,
    model_provider: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    streaming: Optional[bool] = None,
    use_cache: bool = True,
    **kwargs: Any,
) -> BaseChatModel:
    """创建单个聊天模型（无 fallback）

    内部函数，供 get_chat_model() 和 get_chat_model_with_fallback() 共用。
    """
    # 优先从 SystemConfig 数据库读取用户保存的默认模型
    if not model_provider or not model_name:
        system_default = get_system_default_chat_model()
        if system_default:
            if not model_provider:
                model_provider = system_default.get("provider_id")
            if not model_name:
                model_name = system_default.get("model_name")

    model_name = model_name or settings.openai_model
    provider = model_provider or getattr(django_settings, 'AI_DEFAULT_PROVIDER', 'openai')
    temperature = temperature if temperature is not None else settings.openai_temperature
    streaming = streaming if streaming is not None else settings.openai_streaming

    init_kwargs: Dict[str, Any] = {
        "model": model_name,
        "model_provider": provider,
        "temperature": temperature,
        "streaming": streaming,
        "timeout": getattr(django_settings, 'AI_LLM_TIMEOUT', 120.0),
        "max_retries": getattr(django_settings, 'AI_LLM_MAX_RETRIES', 3),
    }

    provider_config = _get_provider_config(provider)
    init_kwargs.update(provider_config)

    if max_tokens is not None:
        init_kwargs["max_tokens"] = max_tokens
    elif settings.openai_max_tokens is not None:
        init_kwargs["max_tokens"] = settings.openai_max_tokens

    rate_limiter = get_rate_limiter()
    if rate_limiter is not None and "rate_limiter" not in kwargs:
        init_kwargs["rate_limiter"] = rate_limiter
        logger.debug("已附加速率限制器")

    init_kwargs.update(kwargs)

    logger.info(
        f"创建聊天模型: {model_name} "
        f"(provider={init_kwargs['model_provider']}, "
        f"temperature={temperature}, streaming={streaming})"
    )

    cache_key = make_cache_key(
        model_name, provider, temperature, streaming,
        init_kwargs.get("max_tokens"),
        api_key=init_kwargs.get("api_key"),
        base_url=init_kwargs.get("base_url"),
        max_retries=init_kwargs.get("max_retries"),
    )

    def _create():
        model = init_chat_model(**init_kwargs)
        model._provider_id = provider
        logger.debug(f"模型创建成功: {model_name}")
        return model

    return _cached_model_creation(cache_key, use_cache, _create)


class LazyFallbackChatModel(BaseChatModel):
    """懒加载 Fallback 模型包装

    不预实例化 fallback 模型，仅在主模型实际失败时按需创建。
    代理主模型的所有方法，失败时自动切换到候选模型。

    兼容性：
    - 提供 .bound 属性（等同于 .primary），兼容旧代码 getattr(model, 'bound', model)
    - 内置 fallback 状态追踪，通过 fallback_detected / actual_provider / actual_model 属性
      替代 FallbackDetectionCallback 的回调检测机制

    Args:
        primary: 主模型实例
        fallback_candidates: 候选列表 [(provider_id, model_name), ...]
        factory: 创建模型的工厂函数
        temperature: 传递给工厂的温度参数
        max_tokens: 传递给工厂的最大 token 参数
        streaming: 传递给工厂的流式参数
    """

    primary: BaseChatModel
    fallback_candidates: List[Tuple[str, str]]
    factory: Callable
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    streaming: Optional[bool] = None
    circuit_breaker_cooldown: float = 30.0

    class Config:
        arbitrary_types_allowed = True

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self._fallback_detected: bool = False
        self._actual_provider: Optional[str] = None
        self._actual_model: Optional[str] = None
        # Fallback 模型缓存（同一对话内复用）
        self._active_fallback_model: Optional[BaseChatModel] = None
        self._fallback_provider_id: Optional[str] = None
        self._fallback_model_name: Optional[str] = None
        # Circuit Breaker 状态
        self._circuit_state: str = "closed"  # closed / open / half_open
        self._circuit_opened_at: float = 0.0
        self._consecutive_failures: int = 0

    @property
    def _llm_type(self) -> str:
        return f"lazy-fallback({self.primary._llm_type})"

    @property
    def bound(self) -> BaseChatModel:
        """兼容 RunnableWithFallbacks.bound 属性，返回底层主模型"""
        return self.primary

    @property
    def fallback_detected(self) -> bool:
        """是否发生了 fallback（与 FallbackDetectionCallback 接口一致）"""
        return self._fallback_detected

    @property
    def actual_provider(self) -> Optional[str]:
        """实际使用的模型 provider（与 FallbackDetectionCallback 接口一致）"""
        return self._actual_provider

    @property
    def actual_model(self) -> Optional[str]:
        """实际使用的模型名称（与 FallbackDetectionCallback 接口一致）"""
        return self._actual_model

    def get_fallback_info(self) -> Optional[Dict[str, str]]:
        """如果检测到降级，返回降级信息；否则返回 None（与 FallbackDetectionCallback 接口一致）"""
        if not self._fallback_detected:
            return None
        return {
            "original_provider": getattr(self.primary, '_provider_id', '') or '',
            "original_model": getattr(self.primary, 'model_name', '') or getattr(self.primary, 'model', '') or '',
            "actual_provider": self._actual_provider or '',
            "actual_model": self._actual_model or '',
            "message": (
                f"模型 {getattr(self.primary, '_provider_id', '')}/{getattr(self.primary, 'model', '')} 运行时失败，"
                f"已自动切换到 {self._actual_provider}/{self._actual_model}"
            ),
        }

    def _mark_fallback(self, provider_id: str, model_name: str) -> None:
        """记录 fallback 发生"""
        self._fallback_detected = True
        self._actual_provider = provider_id
        self._actual_model = model_name
        logger.info(
            f"LazyFallback 降级: {getattr(self.primary, '_provider_id', '')}/{getattr(self.primary, 'model', '')} "
            f"-> {provider_id}/{model_name}"
        )

    def _is_permanent_error(self, error: Exception) -> bool:
        """判断是否为永久性错误（不应重试）"""
        error_str = str(error).lower()
        if "401" in error_str or "403" in error_str or "invalid_credentials" in error_str:
            return True
        if "authentication" in error_str or "unauthorized" in error_str:
            return True
        return False

    def _is_input_error(self, error: Exception) -> bool:
        """判断是否为输入错误（fallback 也无法解决）"""
        error_str = str(error).lower()
        if "400" in error_str or "invalid_request" in error_str:
            return True
        return False

    def _should_try_primary(self) -> bool:
        """判断是否应该尝试主模型（Circuit Breaker 逻辑）"""
        if self._circuit_state == "closed":
            return True
        if self._circuit_state == "open":
            if time.monotonic() - self._circuit_opened_at >= self.circuit_breaker_cooldown:
                self._circuit_state = "half_open"
                logger.info("Circuit Breaker: OPEN -> HALF_OPEN，试探主模型")
                return True
            return False
        if self._circuit_state == "half_open":
            return True
        return False

    def _on_primary_success(self):
        """主模型调用成功"""
        if self._circuit_state != "closed":
            logger.info("Circuit Breaker: -> CLOSED，主模型恢复")
        self._circuit_state = "closed"
        self._consecutive_failures = 0

    def _on_primary_failure(self, error: Exception):
        """主模型调用失败"""
        self._consecutive_failures += 1
        if self._is_permanent_error(error) or self._consecutive_failures >= 2:
            self._circuit_state = "open"
            self._circuit_opened_at = time.monotonic()
            logger.info(
                f"Circuit Breaker: -> OPEN（连续失败 {self._consecutive_failures} 次，"
                f"冷却 {self.circuit_breaker_cooldown}s）"
            )
        elif self._circuit_state == "half_open":
            self._circuit_state = "open"
            self._circuit_opened_at = time.monotonic()
            logger.info("Circuit Breaker: HALF_OPEN -> OPEN，主模型仍不可用")

    def _get_or_create_fallback(self, provider_id: str, model_name: str) -> Optional[BaseChatModel]:
        """获取或创建 fallback 模型（带缓存，同一对话内复用）"""
        if (self._active_fallback_model is not None
            and self._fallback_provider_id == provider_id
            and self._fallback_model_name == model_name):
            return self._active_fallback_model
        fb_model = self._resolve_fallback_model(provider_id, model_name)
        if fb_model is not None:
            self._active_fallback_model = fb_model
            self._fallback_provider_id = provider_id
            self._fallback_model_name = model_name
        return fb_model

    def _resolve_fallback_model(self, provider_id: str, model_name: str) -> Optional[BaseChatModel]:
        """按需创建 fallback 模型实例"""
        try:
            model = self.factory(
                provider_id=provider_id,
                model_name=model_name,
                temperature=self.temperature if self.temperature is not None else settings.openai_temperature,
                max_tokens=self.max_tokens,
                streaming=self.streaming if self.streaming is not None else settings.openai_streaming,
                max_retries=0,
            )
            logger.info(f"懒加载 Fallback 模型已创建: {provider_id}/{model_name}")
            return model
        except Exception as e:
            logger.warning(f"懒加载 Fallback 模型创建失败 {provider_id}/{model_name}: {e}")
            return None

    def _generate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, **kwargs: Any) -> ChatResult:
        # Circuit Breaker: 主模型处于 OPEN 状态时直接使用 fallback
        if not self._should_try_primary():
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    return fb_model._generate(messages, stop=stop, **kwargs)
            raise RuntimeError("主模型不可用且无可用 fallback 模型")

        try:
            result = self.primary._generate(messages, stop=stop, **kwargs)
            self._on_primary_success()
            return result
        except Exception as primary_error:
            if self._is_input_error(primary_error):
                raise primary_error
            self._on_primary_failure(primary_error)
            logger.warning(f"主模型生成失败，尝试 fallback: {primary_error}")
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    try:
                        self._mark_fallback(pid, mname)
                        return fb_model._generate(messages, stop=stop, **kwargs)
                    except Exception as fb_error:
                        self._fallback_detected = False
                        logger.warning(f"Fallback 模型 {pid}/{mname} 也失败: {fb_error}")
                        continue
            raise primary_error

    async def _agenerate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, **kwargs: Any) -> ChatResult:
        # Circuit Breaker: 主模型处于 OPEN 状态时直接使用 fallback
        if not self._should_try_primary():
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    return await fb_model._agenerate(messages, stop=stop, **kwargs)
            raise RuntimeError("主模型不可用且无可用 fallback 模型")

        try:
            result = await self.primary._agenerate(messages, stop=stop, **kwargs)
            self._on_primary_success()
            return result
        except Exception as primary_error:
            if self._is_input_error(primary_error):
                raise primary_error
            self._on_primary_failure(primary_error)
            logger.warning(f"主模型异步生成失败，尝试 fallback: {primary_error}")
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    try:
                        self._mark_fallback(pid, mname)
                        return await fb_model._agenerate(messages, stop=stop, **kwargs)
                    except Exception as fb_error:
                        self._fallback_detected = False
                        logger.warning(f"Fallback 模型 {pid}/{mname} 异步也失败: {fb_error}")
                        continue
            raise primary_error

    def _stream(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        # Circuit Breaker: 主模型处于 OPEN 状态时直接使用 fallback
        if not self._should_try_primary():
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    yield from fb_model._stream(messages, stop=stop, **kwargs)
                    return
            raise RuntimeError("主模型不可用且无可用 fallback 模型")

        try:
            chunks = 0
            for chunk in self.primary._stream(messages, stop=stop, **kwargs):
                chunks += 1
                yield chunk
            self._on_primary_success()
            if chunks:
                logger.debug(f"主模型流式返回 {chunks} 个 chunk")
        except Exception as primary_error:
            if self._is_input_error(primary_error):
                raise primary_error
            self._on_primary_failure(primary_error)
            logger.warning(f"主模型流式失败，尝试 fallback: {primary_error}")
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    try:
                        self._mark_fallback(pid, mname)
                        chunks = 0
                        for chunk in fb_model._stream(messages, stop=stop, **kwargs):
                            chunks += 1
                            yield chunk
                        logger.info(f"Fallback 流式返回 {chunks} 个 chunk: {pid}/{mname}")
                        return
                    except Exception as fb_error:
                        self._fallback_detected = False
                        logger.warning(f"Fallback 模型 {pid}/{mname} 流式也失败: {fb_error}")
                        continue
            raise primary_error

    async def _astream(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        # Circuit Breaker: 主模型处于 OPEN 状态时直接使用 fallback
        if not self._should_try_primary():
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    async for chunk in fb_model._astream(messages, stop=stop, **kwargs):
                        yield chunk
                    return
            raise RuntimeError("主模型不可用且无可用 fallback 模型")

        try:
            chunks = 0
            async for chunk in self.primary._astream(messages, stop=stop, **kwargs):
                chunks += 1
                yield chunk
            self._on_primary_success()
            if chunks:
                logger.debug(f"主模型异步流式返回 {chunks} 个 chunk")
        except Exception as primary_error:
            if self._is_input_error(primary_error):
                raise primary_error
            self._on_primary_failure(primary_error)
            logger.warning(f"主模型异步流式失败，尝试 fallback: {primary_error}")
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    try:
                        self._mark_fallback(pid, mname)
                        chunks = 0
                        async for chunk in fb_model._astream(messages, stop=stop, **kwargs):
                            chunks += 1
                            yield chunk
                        logger.info(f"Fallback 异步流式返回 {chunks} 个 chunk: {pid}/{mname}")
                        return
                    except Exception as fb_error:
                        self._fallback_detected = False
                        logger.warning(f"Fallback 模型 {pid}/{mname} 异步流式也失败: {fb_error}")
                        continue
            raise primary_error

    def invoke(self, input: Any, config: Optional[RunnableConfig] = None, **kwargs: Any) -> Any:
        # Circuit Breaker: 主模型处于 OPEN 状态时直接使用 fallback
        if not self._should_try_primary():
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    return fb_model.invoke(input, config=config, **kwargs)
            raise RuntimeError("主模型不可用且无可用 fallback 模型")

        try:
            result = self.primary.invoke(input, config=config, **kwargs)
            self._on_primary_success()
            return result
        except Exception as primary_error:
            if self._is_input_error(primary_error):
                raise primary_error
            self._on_primary_failure(primary_error)
            logger.warning(f"主模型 invoke 失败，尝试 fallback: {primary_error}")
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    try:
                        self._mark_fallback(pid, mname)
                        return fb_model.invoke(input, config=config, **kwargs)
                    except Exception as fb_error:
                        self._fallback_detected = False
                        logger.warning(f"Fallback 模型 {pid}/{mname} invoke 也失败: {fb_error}")
                        continue
            raise primary_error

    async def ainvoke(self, input: Any, config: Optional[RunnableConfig] = None, **kwargs: Any) -> Any:
        # Circuit Breaker: 主模型处于 OPEN 状态时直接使用 fallback
        if not self._should_try_primary():
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    return await fb_model.ainvoke(input, config=config, **kwargs)
            raise RuntimeError("主模型不可用且无可用 fallback 模型")

        try:
            result = await self.primary.ainvoke(input, config=config, **kwargs)
            self._on_primary_success()
            return result
        except Exception as primary_error:
            if self._is_input_error(primary_error):
                raise primary_error
            self._on_primary_failure(primary_error)
            logger.warning(f"主模型 ainvoke 失败，尝试 fallback: {primary_error}")
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    try:
                        self._mark_fallback(pid, mname)
                        return await fb_model.ainvoke(input, config=config, **kwargs)
                    except Exception as fb_error:
                        self._fallback_detected = False
                        logger.warning(f"Fallback 模型 {pid}/{mname} ainvoke 也失败: {fb_error}")
                        continue
            raise primary_error

    def stream(self, input: Any, config: Optional[RunnableConfig] = None, **kwargs: Any) -> Iterator[Any]:
        # Circuit Breaker: 主模型处于 OPEN 状态时直接使用 fallback
        if not self._should_try_primary():
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    yield from fb_model.stream(input, config=config, **kwargs)
                    return
            raise RuntimeError("主模型不可用且无可用 fallback 模型")

        try:
            yield from self.primary.stream(input, config=config, **kwargs)
            self._on_primary_success()
        except Exception as primary_error:
            if self._is_input_error(primary_error):
                raise primary_error
            self._on_primary_failure(primary_error)
            logger.warning(f"主模型 stream 失败，尝试 fallback: {primary_error}")
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    try:
                        self._mark_fallback(pid, mname)
                        yield from fb_model.stream(input, config=config, **kwargs)
                        return
                    except Exception as fb_error:
                        self._fallback_detected = False
                        logger.warning(f"Fallback 模型 {pid}/{mname} stream 也失败: {fb_error}")
                        continue
            raise primary_error

    async def astream(self, input: Any, config: Optional[RunnableConfig] = None, **kwargs: Any) -> AsyncIterator[Any]:
        # Circuit Breaker: 主模型处于 OPEN 状态时直接使用 fallback
        if not self._should_try_primary():
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    async for chunk in fb_model.astream(input, config=config, **kwargs):
                        yield chunk
                    return
            raise RuntimeError("主模型不可用且无可用 fallback 模型")

        try:
            async for chunk in self.primary.astream(input, config=config, **kwargs):
                yield chunk
            self._on_primary_success()
        except Exception as primary_error:
            if self._is_input_error(primary_error):
                raise primary_error
            self._on_primary_failure(primary_error)
            logger.warning(f"主模型 astream 失败，尝试 fallback: {primary_error}")
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    try:
                        self._mark_fallback(pid, mname)
                        async for chunk in fb_model.astream(input, config=config, **kwargs):
                            yield chunk
                        return
                    except Exception as fb_error:
                        self._fallback_detected = False
                        logger.warning(f"Fallback 模型 {pid}/{mname} astream 也失败: {fb_error}")
                        continue
            raise primary_error

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        """将 bind_tools 代理到主模型

        注意：bind_tools 返回 RunnableBinding 而非 BaseChatModel，
        因此无法再包装为 LazyFallbackChatModel（Pydantic 验证 primary: BaseChatModel 会失败）。
        直接返回 RunnableBinding，此时 fallback 机制不再生效，
        但 LangGraph 的 create_react_agent 内部会调用此方法，且工具绑定后
        不应再切换模型（工具配置可能不同）。
        """
        return self.primary.bind_tools(tools, **kwargs)

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable:
        """将 with_structured_output 代理到主模型（不支持 fallback）"""
        return self.primary.with_structured_output(schema, **kwargs)

    @property
    def _provider_id(self) -> Optional[str]:
        return getattr(self.primary, '_provider_id', None)


def get_chat_model(
    model_name: Optional[str] = None,
    model_provider: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    streaming: Optional[bool] = None,
    enable_fallback: bool = True,
    **kwargs: Any,
) -> BaseChatModel:
    """创建聊天模型，默认带自动 fallback（懒加载）

    主模型调用失败时自动切换到下一个可用模型（使用 LazyFallbackChatModel）。
    Fallback 模型仅在主模型实际失败时才实例化，避免创建未使用的模型。
    所有模块统一通过此函数获取模型，自动获得 fallback 能力。

    设置 enable_fallback=False 可禁用 fallback（用于测试等场景）。

    Args:
        model_name: 模型名称
        model_provider: 模型提供商
        temperature: 温度参数
        max_tokens: 最大 token 数
        streaming: 是否流式
        enable_fallback: 是否启用 fallback，默认 True
        **kwargs: 其他参数

    Returns:
        BaseChatModel 或 LazyFallbackChatModel 实例

    Raises:
        RuntimeError: 所有模型都不可用时（enable_fallback=True）
    """
    if not enable_fallback:
        return _create_single_chat_model(
            model_name=model_name,
            model_provider=model_provider,
            temperature=temperature,
            max_tokens=max_tokens,
            streaming=streaming,
            **kwargs,
        )

    # === Fallback 逻辑（懒加载） ===
    # 优先从 SystemConfig 数据库读取用户保存的默认模型
    if not model_provider or not model_name:
        system_default = get_system_default_chat_model()
        if system_default:
            if not model_provider:
                model_provider = system_default.get("provider_id")
            if not model_name:
                model_name = system_default.get("model_name")

    resolved_provider = model_provider or getattr(django_settings, 'AI_DEFAULT_PROVIDER', 'openai')
    resolved_model_name = model_name or settings.openai_model

    # 1. 尝试创建主模型（max_retries=0 快速失败，由 fallback 接管）
    primary_model = None
    creation_errors: List[str] = []

    try:
        primary_model = _create_single_chat_model(
            model_name=model_name,
            model_provider=model_provider,
            temperature=temperature,
            max_tokens=max_tokens,
            streaming=streaming,
            max_retries=0,
            **kwargs,
        )
    except Exception as e:
        creation_errors.append(f"{resolved_provider}/{resolved_model_name}: {e}")
        logger.warning(f"主模型创建失败，尝试 fallback: {resolved_provider}/{resolved_model_name} - {e}")

    # 2. 获取 fallback 候选列表（不预实例化）
    candidates = get_fallback_candidates(
        exclude_provider=resolved_provider,
        exclude_model=resolved_model_name,
    )

    # 3. 如果主模型创建失败，立即实例化第一个可用候选作为主模型
    if primary_model is None:
        for pid, mname in candidates:
            try:
                primary_model = get_chat_model_by_provider(
                    provider_id=pid,
                    model_name=mname,
                    temperature=temperature if temperature is not None else settings.openai_temperature,
                    max_tokens=max_tokens,
                    streaming=streaming if streaming is not None else settings.openai_streaming,
                    max_retries=0,
                )
                # 将已使用的候选从列表中移除
                remaining_candidates = [(p, m) for p, m in candidates if not (p == pid and m == mname)]
                candidates = remaining_candidates
                logger.info(f"主模型不可用，提升 fallback 模型作为主模型: {pid}/{mname}")
                break
            except Exception as e:
                creation_errors.append(f"{pid}/{mname}: {e}")
                logger.warning(f"Fallback 模型创建失败 {pid}/{mname}: {e}")
                continue

        if primary_model is None:
            error_detail = "; ".join(creation_errors)
            logger.error(f"所有模型均不可用: {error_detail}")
            raise RuntimeError(
                f"模型连接超时，所有已配置的模型均不可用。"
                f"已尝试: {error_detail}。请检查 API Key 配置和网络连接。"
            )

    # 4. 使用 LazyFallbackChatModel 包装（fallback 模型延迟到运行时按需创建）
    if candidates:
        lazy_model = LazyFallbackChatModel(
            primary=primary_model,
            fallback_candidates=candidates,
            factory=get_chat_model_by_provider,
            temperature=temperature if temperature is not None else settings.openai_temperature,
            max_tokens=max_tokens,
            streaming=streaming if streaming is not None else settings.openai_streaming,
        )
        logger.info(
            f"已配置模型 fallback（懒加载）: 主模型 + {len(candidates)} 个候选"
        )
        return lazy_model

    logger.warning("无可用 fallback 模型，仅使用主模型（无自动切换）")
    return primary_model


def get_streaming_model(
    model_name: Optional[str] = None,
    model_provider: Optional[str] = None,
    temperature: Optional[float] = None,
    **kwargs: Any,
) -> BaseChatModel:
    return get_chat_model(
        model_name=model_name,
        model_provider=model_provider,
        temperature=temperature,
        streaming=True,
        **kwargs,
    )


def get_structured_output_model(
    model_name: Optional[str] = None,
    model_provider: Optional[str] = None,
    temperature: float = 0.0,
    response_format: Optional[Any] = None,
    **kwargs: Any,
) -> BaseChatModel:
    model = get_chat_model(
        model_name=model_name,
        model_provider=model_provider,
        temperature=temperature,
        streaming=False,
        **kwargs,
    )

    if response_format is not None:
        try:
            base = model.bound if hasattr(model, 'bound') else model
            model = base.with_structured_output(response_format)
            logger.info(f"已绑定结构化输出: {getattr(response_format, '__name__', str(response_format))}")
        except Exception as e:
            logger.warning(f"绑定结构化输出失败: {e}，将使用普通模式")

    return model


def get_model_config(preset: str) -> dict:
    """从统一配置获取模型预设配置"""
    presets = get_model_presets()
    return presets.get(preset, {})


def _get_provider_config(provider: str) -> Dict[str, Any]:
    """从数据库获取 provider 的 API 配置（api_key, base_url）"""
    for provider_id, cfg in get_model_registry().items():
        if cfg["provider"] == provider or provider_id == provider:
            result: Dict[str, Any] = {}
            key_attr = cfg.get("api_key_attr")
            if key_attr:
                api_key = getattr(settings, key_attr, "")
                if api_key:
                    result["api_key"] = api_key
            base_url_attr = cfg.get("base_url_attr")
            if base_url_attr:
                base_url = getattr(settings, base_url_attr, "")
                if base_url:
                    result["base_url"] = base_url
            return result

    # 硬编码 fallback（数据库无数据时兜底）
    if provider == "openai":
        return {
            "api_key": settings.openai_api_key,
            "base_url": settings.openai_api_base,
        }
    elif provider == "anthropic":
        return {
            "api_key": getattr(settings, "anthropic_api_key", ""),
        }
    return {}


def get_model_by_preset(preset: str = "default", **kwargs: Any) -> BaseChatModel:
    presets = get_model_presets()
    if preset not in presets:
        available = ", ".join(presets.keys())
        raise ValueError(f"未知的预设: {preset}. 可用预设: {available}")

    config = presets[preset].copy()
    config.pop("description", None)
    model_provider = config.pop("model_provider", None)
    config.update(kwargs)

    logger.info(f"使用预设模型配置: {preset}")
    return get_chat_model(model_provider=model_provider, **config)


def get_model_string(
    model_name: Optional[str] = None,
    provider: Optional[str] = None,
) -> str:
    """生成 ``"<provider>:<model_name>"`` 字符串。

    解析优先级（与 :func:`get_chat_model` 对齐）：

    1. 显式传入的 ``provider`` / ``model_name``
    2. ``SystemConfig.default_chat_model``（用户在 Admin 设置的默认聊天模型）
    3. ``settings.openai_model`` + ``django_settings.AI_DEFAULT_PROVIDER`` 兜底
    """
    # 1. 优先从 SystemConfig 数据库读取用户配置的默认模型
    if not provider or not model_name:
        system_default = get_system_default_chat_model()
        if system_default:
            provider = provider or system_default.get("provider_id")
            model_name = model_name or system_default.get("model_name")

    # 2. 回退到 settings（保持向后兼容）
    provider = provider or getattr(django_settings, 'AI_DEFAULT_PROVIDER', 'openai')
    model_name = model_name or settings.openai_model

    model_string = f"{provider}:{model_name}"
    logger.debug(f"生成模型标识符: {model_string}")
    return model_string


def get_chat_model_by_provider(
    provider_id: str,
    model_name: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    streaming: Optional[bool] = None,
    special_params: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> BaseChatModel:
    registry = PROVIDER_REGISTRY.get(provider_id) or get_provider_config(provider_id)
    if registry is None or not registry:
        available = ", ".join(set(list(PROVIDER_REGISTRY.keys()) + list(get_model_registry().keys())))
        raise ValueError(f"未知的提供商: {provider_id}. 可用: {available}")

    api_key_attr = registry.get("api_key_attr")
    if api_key_attr:
        api_key = getattr(settings, api_key_attr, "")
        if not api_key or not api_key.strip():
            raise ValueError(f"提供商 {registry['label']} 的 API Key 未配置")
    else:
        api_key = ""  # 本地 provider（如 Ollama）无需 API Key

    resolved_model = model_name or registry["default_model"]
    provider = registry["provider"]
    resolved_temp = temperature if temperature is not None else settings.openai_temperature
    resolved_streaming = streaming if streaming is not None else settings.openai_streaming

    init_kwargs: Dict[str, Any] = {
        "model": resolved_model,
        "model_provider": provider,
        "temperature": resolved_temp,
        "streaming": resolved_streaming,
        "timeout": getattr(django_settings, 'AI_LLM_TIMEOUT', 120.0),
        "max_retries": getattr(django_settings, 'AI_LLM_MAX_RETRIES', 3),
        "api_key": api_key,
    }

    if registry.get("base_url_attr"):
        base_url = getattr(settings, registry["base_url_attr"], "")
        if base_url:
            init_kwargs["base_url"] = base_url

    if max_tokens is not None:
        init_kwargs["max_tokens"] = max_tokens
    elif settings.openai_max_tokens is not None:
        init_kwargs["max_tokens"] = settings.openai_max_tokens

    rate_limiter = get_rate_limiter()
    if rate_limiter is not None and "rate_limiter" not in kwargs:
        init_kwargs["rate_limiter"] = rate_limiter

    if special_params:
        model_kwargs: Dict[str, Any] = {}
        extra_body: Dict[str, Any] = {}
        for param_key, param_value in special_params.items():
            if param_key in registry.get("special_params", {}):
                param_cfg = registry["special_params"][param_key]
                kwarg_name = param_cfg.get("model_kwarg", param_key)
                pass_mode = param_cfg.get("pass_mode", "model_kwargs")
                if pass_mode == "top_level":
                    init_kwargs[kwarg_name] = param_value
                elif pass_mode == "extra_body":
                    extra_body[kwarg_name] = param_value
                else:
                    model_kwargs[kwarg_name] = param_value

        # DeepSeek: reasoning_effort 仅在 thinking 已启用时才有意义
        # 如果 reasoning_effort 存在但 thinking 未启用，移除 reasoning_effort 而非强制启用 thinking
        if "reasoning_effort" in special_params and "thinking" not in special_params:
            thinking_cfg = registry.get("special_params", {}).get("thinking")
            if thinking_cfg:
                # thinking 未启用，移除 reasoning_effort
                special_params.pop("reasoning_effort", None)
                init_kwargs.pop("reasoning_effort", None)
                extra_body.pop("reasoning_effort", None)
                model_kwargs.pop("reasoning_effort", None)
                logger.debug("reasoning_effort 已设置但 thinking 未启用，移除 reasoning_effort")

        if model_kwargs:
            init_kwargs["model_kwargs"] = model_kwargs
        if extra_body:
            init_kwargs["extra_body"] = extra_body

        # 深度思考模式：Provider 感知参数注入
        if model_supports_capability(provider_id, resolved_model, "deep_thinking") and is_thinking_enabled(special_params, provider_id):
            # DeepSeek: 思考模式不支持 temperature/top_p
            if provider_id == "deepseek":
                init_kwargs.pop("temperature", None)
                init_kwargs.pop("top_p", None)
                logger.debug("DeepSeek 深度思考模式已启用，移除 temperature/top_p 参数")
            # Ollama: 注入 reasoning=True（如果 special_params 机制未传递）
            if provider == "ollama" and "reasoning" not in init_kwargs:
                init_kwargs["reasoning"] = True
                logger.debug("Ollama 深度思考模式已启用，注入 reasoning=True")
            # Anthropic: 注入 thinking 参数（如果 special_params 机制未传递）
            if provider == "anthropic" and "thinking" not in init_kwargs:
                init_kwargs["thinking"] = {"type": "enabled", "budget_tokens": 10000}
                logger.debug("Anthropic 扩展思考模式已启用，注入 thinking 参数")

    if model_supports_capability(provider_id, resolved_model, "deep_thinking"):
        apply_reasoning_patch_if_needed()

    init_kwargs.update(kwargs)

    use_cache = init_kwargs.pop("use_cache", True)

    logger.info(
        f"创建模型: {resolved_model} "
        f"(provider_id={provider_id}, provider={provider}, "
        f"temperature={resolved_temp}, streaming={resolved_streaming})"
    )

    special_suffix = ""
    if special_params:
        import json as _json
        special_suffix = f":sp{_json.dumps(special_params, sort_keys=True)}"

    cache_key = make_cache_key(
        resolved_model, provider, resolved_temp, resolved_streaming,
        init_kwargs.get("max_tokens"), special_suffix,
        api_key=api_key,
        base_url=init_kwargs.get("base_url"),
        max_retries=init_kwargs.get("max_retries"),
    )

    def _create():
        # Groq ChatGroq 类在 Pydantic v2 下缺少 bind_tools field，
        # 导致 init_chat_model 内部设置该属性时抛出 ValidationError。
        # 在实例化前先给类添加 model_field 声明。
        if provider == "groq":
            _ensure_groq_bind_tools_field()

        # Ollama 本地服务无需 API Key，走专用 provider
        if provider == "ollama":
            from Django_xm.apps.ai_engine.providers.ollama import create_chat_model
            # 从 init_kwargs 中过滤掉 init_chat_model 专用字段，避免与 provider 内部重复传参
            ollama_kwargs = {
                k: v for k, v in init_kwargs.items()
                if k not in (
                    "model", "model_provider", "temperature", "streaming",
                    "api_key", "rate_limiter", "max_tokens", "timeout", "max_retries",
                    "base_url",
                )
            }
            model = create_chat_model(
                model=resolved_model,
                temperature=resolved_temp,
                streaming=resolved_streaming,
                **ollama_kwargs,
            )
        else:
            model = init_chat_model(**init_kwargs)
        model._provider_id = provider
        logger.debug(f"模型创建成功: {resolved_model}")

        if provider == "groq":
            model = patch_groq_model(model)

        return model

    return _cached_model_creation(cache_key, use_cache, _create, error_context=f"provider_id={provider_id}")


_groq_field_patched = False


def _ensure_groq_bind_tools_field() -> None:
    """确保 ChatGroq 类有 bind_tools 方法（Pydantic v2 兼容）

    langchain-groq 的 ChatGroq 继承自 Pydantic BaseModel，
    但未声明 bind_tools 字段。当 init_chat_model 尝试在构造时
    设置 bind_tools 属性时，Pydantic v2 会拒绝并抛出 ValidationError。

    Pydantic v2 不允许在实例上动态 setattr 添加未声明字段，
    但允许在类上添加方法（方法不算字段）。
    此函数在类级别添加 bind_tools 方法实现，
    避免 init_chat_model 内部尝试在实例上 setattr。

    注意：Groq 模型并非所有版本都缺少 bind_tools 字段，
    此处仅在确实缺失时做兼容性处理。
    """
    global _groq_field_patched
    if _groq_field_patched:
        return

    try:
        from langchain_groq import ChatGroq
    except ImportError as e:
        logger.debug(f"langchain-groq 未安装: {e}")
        _groq_field_patched = True
        return

    try:
        # 方案：将 bind_tools 注册为类的 __getattribute__ 处理项
        # 由于 Pydantic v2 BaseModel 的字段优先于 __dict__，
        # 我们需要让类层面同时存在字段（避免 init_chat_model 报错）
        # 和方法（避免调用 None 报错）
        from pydantic import Field

        if "bind_tools" not in ChatGroq.model_fields:
            # 添加一个默认为 None 的字段以避免 init_chat_model 构造时
            # "object has no field bind_tools" 错误
            ChatGroq.model_fields["bind_tools"] = Field(default=None)  # type: ignore[assignment]

        # 同时给类添加一个真正的 bind_tools 方法实现
        # 这个方法在实例访问 bind_tools 时优先于字段（因为 Pydantic v2
        # 的字段访问通过 __class_getitem__ 等机制，方法直接定义在类上）
        if not hasattr(ChatGroq, "bind_tools") or ChatGroq.__dict__.get("bind_tools") is None:
            def _bind_tools_default(self, tools, *, tool_choice=None, **kwargs):
                """Groq 模型 bind_tools 默认实现：委托给 bind()"""
                return self.bind(tools=tools, tool_choice=tool_choice, **kwargs)
            ChatGroq.bind_tools = _bind_tools_default  # type: ignore[attr-defined]

        try:
            ChatGroq.model_rebuild(force=True)
        except Exception as rebuild_err:
            logger.debug(f"ChatGroq.model_rebuild 失败（不影响功能）: {rebuild_err}")

        logger.debug("ChatGroq 类已添加 bind_tools 字段声明和方法实现")
        _groq_field_patched = True
    except Exception as e:
        logger.warning(f"ChatGroq bind_tools 字段补丁失败: {e}，将尝试其他方式")
        _groq_field_patched = True


def test_model_connection(
    provider_id: str,
    model_name: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    try:
        model = get_chat_model_by_provider(
            provider_id=provider_id,
            model_name=model_name,
            temperature=0.0,
            max_tokens=10,
            streaming=False,
            use_cache=False,
            **kwargs,
        )
        from langchain_core.messages import HumanMessage

        from langchain_core.globals import get_llm_cache, set_llm_cache
        original_cache = get_llm_cache()
        try:
            set_llm_cache(None)
            response = model.invoke([HumanMessage(content="Hi")])
        finally:
            set_llm_cache(original_cache)

        registry = PROVIDER_REGISTRY.get(provider_id) or get_provider_config(provider_id)
        return {
            "success": True,
            "message": f"模型连接成功",
            "model_info": {
                "provider_id": provider_id,
                "model_name": model_name or registry.get("default_model", ""),
                "response_preview": str(response.content)[:100],
            },
        }
    except Exception as e:
        registry = PROVIDER_REGISTRY.get(provider_id) or get_provider_config(provider_id)
        return {
            "success": False,
            "message": f"模型连接失败: {str(e)}",
            "model_info": {
                "provider_id": provider_id,
                "model_name": model_name or registry.get("default_model", ""),
            },
        }


_helper_model_cache: Optional[BaseChatModel] = None


def get_helper_model() -> Optional[BaseChatModel]:
    """获取辅助模型（带 fallback 包装）

    辅助模型用于非主要 Agent 场景（MultiQuery、Map-Reduce、意图分类、压缩等）。
    返回 LazyFallbackChatModel 包装，具备 Circuit Breaker 和自动降级能力。
    """
    global _helper_model_cache
    if _helper_model_cache is not None:
        return _helper_model_cache

    helper_provider = ""
    helper_model_name = ""
    helper_temp = 0.0
    helper_max_tokens = 256

    # 1. 优先从 SystemConfig 数据库读取（持久化，重启不丢失）
    try:
        from Django_xm.apps.ai_engine.models import SystemConfig
        helper_config = SystemConfig.get_value("helper_model", {})
        if helper_config.get("provider_id"):
            helper_provider = helper_config["provider_id"]
            helper_model_name = helper_config.get("model_name", "")
    except Exception:
        pass

    # 2. 回退到运行时内存（兼容旧逻辑）
    if not helper_provider:
        helper_provider = getattr(django_settings, 'AI_HELPER_MODEL_PROVIDER', '')
        helper_model_name = getattr(django_settings, 'AI_HELPER_MODEL_NAME', '')
    helper_temp = getattr(django_settings, 'AI_HELPER_MODEL_TEMPERATURE', 0.0)
    helper_max_tokens = getattr(django_settings, 'AI_HELPER_MODEL_MAX_TOKENS', 256)

    def _wrap_with_fallback(primary: BaseChatModel, provider: str, model: str) -> BaseChatModel:
        """将辅助模型包装为 LazyFallbackChatModel"""
        candidates = get_fallback_candidates(exclude_provider=provider, exclude_model=model)
        if candidates:
            wrapped = LazyFallbackChatModel(
                primary=primary,
                fallback_candidates=candidates,
                factory=get_chat_model_by_provider,
                temperature=helper_temp,
                max_tokens=helper_max_tokens,
                streaming=False,
            )
            logger.info(f"辅助模型(+fallback): {provider}/{model} + {len(candidates)} 候选")
            return wrapped
        return primary

    if helper_provider and helper_model_name:
        try:
            primary = get_chat_model_by_provider(
                provider_id=helper_provider,
                model_name=helper_model_name,
                temperature=helper_temp,
                max_tokens=helper_max_tokens,
                streaming=False,
            )
            _helper_model_cache = _wrap_with_fallback(primary, helper_provider, helper_model_name)
            logger.info(f"辅助模型(用户配置): {helper_provider}/{helper_model_name}")
            return _helper_model_cache
        except Exception as e:
            logger.warning(f"用户配置的辅助模型不可用: {e}")

    for candidate in HELPER_MODEL_PRIORITY:
        pid = candidate["provider"]
        mname = candidate["model"]
        if not registry_is_provider_available(pid):
            continue
        try:
            primary = get_chat_model_by_provider(
                provider_id=pid,
                model_name=mname,
                temperature=0.0,
                max_tokens=helper_max_tokens,
                streaming=False,
            )
            _helper_model_cache = _wrap_with_fallback(primary, pid, mname)
            logger.info(f"辅助模型(自动选择): {pid}/{mname}")
            return _helper_model_cache
        except Exception as e:
            logger.warning(f"辅助模型 {pid}/{mname} 不可用: {e}")
            continue

    logger.warning("无可用辅助模型，循环检测 Layer 4 降级")
    return None


# ==================== 数据库配置读取 ====================

def get_system_default_chat_model() -> Optional[Dict[str, str]]:
    """从 SystemConfig 数据库读取用户偏好的默认聊天模型

    Returns:
        {"provider_id": "openai", "model_name": "gpt-4o-mini"} 或 None
    """
    try:
        from Django_xm.apps.ai_engine.models import SystemConfig
        config = SystemConfig.get_value("default_chat_model", {})
        if config.get("provider_id"):
            return config
    except Exception:
        pass
    return None


# ==================== 模型 Fallback 机制 ====================
# 使用 LangChain 原生 with_fallbacks() 实现模型自动切换
# 参考: https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.fallbacks.RunnableWithFallbacks.html


def _is_connection_error(exc: Exception) -> bool:
    """判断异常是否为可触发 fallback 的连接/认证类错误

    仅对以下错误触发 fallback，其他错误（如参数错误）不触发：
    - 401/403 认证/权限错误（余额不足、Key 无效等）
    - 429 速率限制
    - 连接超时 / 网络不可达
    """
    error_type = type(exc).__name__
    error_module = type(exc).__module__

    # OpenAI SDK 错误
    if "openai" in error_module:
        if error_type in (
            "AuthenticationError",
            "PermissionDeniedError",
            "RateLimitError",
            "APIConnectionError",
            "APITimeoutError",
        ):
            return True

    # Anthropic SDK 错误
    if "anthropic" in error_module:
        if error_type in (
            "AuthenticationError",
            "PermissionDeniedError",
            "RateLimitError",
            "APIConnectionError",
            "APITimeoutError",
        ):
            return True

    # 通用网络错误
    if error_type in (
        "ConnectionError",
        "TimeoutError",
        "ConnectTimeoutError",
        "SSLError",
    ):
        return True

    # httpx / urllib3 连接错误
    if "ConnectTimeout" in error_type or "ConnectionError" in error_type:
        return True

    # HTTP 状态码判断
    status_code = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    if status_code in (401, 403, 429, 502, 503):
        return True

    return False


def get_fallback_candidates(
    exclude_provider: Optional[str] = None,
    exclude_model: Optional[str] = None,
) -> List[Tuple[str, str]]:
    """获取可用的 fallback 模型候选列表

    优先级：用户配置的降级模型 > HELPER_MODEL_PRIORITY 中第一个可用的（兜底）
    不再自动拉取所有 HELPER_MODEL_PRIORITY 和 MODEL_REGISTRY 中的模型，
    避免创建用户未配置的模型实例。
    """
    candidates: List[Tuple[str, str]] = []

    # 0. 最高优先级：用户配置的降级模型
    try:
        from Django_xm.apps.ai_engine.models import SystemConfig
        fb_config = SystemConfig.get_value("fallback_chat_model", {})
        fb_provider = fb_config.get("provider_id", "")
        fb_model = fb_config.get("model_name", "")
        if fb_provider and fb_model:
            if not (fb_provider == exclude_provider and fb_model == exclude_model):
                if registry_is_provider_available(fb_provider):
                    candidates.append((fb_provider, fb_model))
    except Exception:
        pass

    # 1. 兜底：若用户未配置降级模型，从 HELPER_MODEL_PRIORITY 中取第一个可用的
    if not candidates:
        for item in HELPER_MODEL_PRIORITY:
            pid = item["provider"]
            mname = item["model"]
            if pid == exclude_provider and mname == exclude_model:
                continue
            if registry_is_provider_available(pid):
                candidates.append((pid, mname))
                break  # 只取一个兜底

    return candidates


def get_chat_model_with_fallback(
    model_name: Optional[str] = None,
    model_provider: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    streaming: Optional[bool] = None,
    **kwargs: Any,
) -> BaseChatModel:
    """创建带自动 fallback 的聊天模型（兼容接口）

    已废弃：get_chat_model() 默认已启用 fallback，直接使用 get_chat_model() 即可。
    此函数保留仅为向后兼容。
    """
    return get_chat_model(
        model_name=model_name,
        model_provider=model_provider,
        temperature=temperature,
        max_tokens=max_tokens,
        streaming=streaming,
        enable_fallback=True,
        **kwargs,
    )


def get_structured_model_with_fallback(
    schema: Any,
    model_name: Optional[str] = None,
    model_provider: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    streaming: Optional[bool] = False,  # 结构化输出默认禁用流式
    **kwargs: Any,
) -> Any:
    """创建带 fallback 的结构化输出模型

    对主模型和每个 fallback 模型分别应用 with_structured_output，
    手动实现 fallback 逻辑（不使用 with_fallbacks，因为 with_structured_output
    会包装异常导致 with_fallbacks 无法正确触发）。

    返回的对象支持 invoke() / ainvoke()，用法与 model.with_structured_output(schema) 一致。

    重要：结构化输出场景默认 streaming=False，因为：
    1. 流式 + with_structured_output(嵌套 Pydantic Schema) 容易返回 None
    2. 结构化输出需要累积所有 chunks 才能解析，无流式必要
    3. 非流式更稳定，避免 deepseek/baidu_qianfan 等模型在流式下的解析失败

    Args:
        schema: Pydantic BaseModel 类，定义结构化输出的格式
        model_name: 模型名称
        model_provider: 模型提供商
        temperature: 温度参数
        max_tokens: 最大 token 数
        streaming: 是否流式（默认 False）
        **kwargs: 其他参数

    Returns:
        StructuredModelWithFallback 实例，支持 invoke/ainvoke

    Raises:
        RuntimeError: 所有模型都不可用时
    """
    # 优先从 SystemConfig 数据库读取用户保存的默认模型
    if not model_provider or not model_name:
        system_default = get_system_default_chat_model()
        if system_default:
            if not model_provider:
                model_provider = system_default.get("provider_id")
            if not model_name:
                model_name = system_default.get("model_name")

    resolved_provider = model_provider or getattr(django_settings, 'AI_DEFAULT_PROVIDER', 'openai')
    resolved_model_name = model_name or settings.openai_model

    structured_models: List[Tuple[str, str, Any]] = []
    creation_errors: List[str] = []

    # 1. 创建主模型
    try:
        # DeepSeek thinking 模式与 with_structured_output (tool_choice) 不兼容
        # 结构化输出场景下，显式禁用 thinking 模式
        # DeepSeek API 要求格式: {"thinking": {"type": "disabled"}}
        primary_special_params = None
        if resolved_provider == "deepseek":
            primary_special_params = {"thinking": {"type": "disabled"}}

        if primary_special_params:
            # 需要通过 get_chat_model_by_provider 创建（支持 special_params）
            primary_model = get_chat_model_by_provider(
                provider_id=resolved_provider,
                model_name=resolved_model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                streaming=streaming,
                max_retries=0,
                special_params=primary_special_params,
            )
        else:
            primary_model = _create_single_chat_model(
                model_name=model_name,
                model_provider=model_provider,
                temperature=temperature,
                max_tokens=max_tokens,
                streaming=streaming,
                max_retries=0,
                **kwargs,
            )
        primary_structured = primary_model.with_structured_output(schema)
        structured_models.append((resolved_provider, resolved_model_name, primary_structured))
        logger.debug(f"主模型结构化输出已配置: {resolved_provider}/{resolved_model_name}")
    except Exception as e:
        creation_errors.append(f"{resolved_provider}/{resolved_model_name}: {e}")
        logger.warning(f"主模型结构化输出配置失败: {resolved_provider}/{resolved_model_name} - {e}")

    # 2. 获取 fallback 候选列表（不预实例化，延迟到运行时按需创建）
    candidates = get_fallback_candidates(
        exclude_provider=resolved_provider,
        exclude_model=resolved_model_name,
    )

    # 3. 检查是否有可用模型
    if not structured_models and not candidates:
        error_detail = "; ".join(creation_errors)
        logger.error(f"所有结构化模型均不可用: {error_detail}")
        raise RuntimeError(
            f"模型连接超时，所有已配置的模型均不可用。"
            f"已尝试: {error_detail}。请检查 API Key 配置和网络连接。"
            )

    logger.info(f"已配置结构化模型 fallback（懒加载）: 主模型 + {len(candidates)} 个候选")
    return StructuredModelWithFallback(structured_models, creation_errors, lazy_candidates=candidates, schema=schema)


class StructuredModelWithFallback:
    """结构化输出模型 + 手动 fallback（支持懒加载）

    不使用 LangChain 的 with_fallbacks()，因为 with_structured_output
    会包装异常导致 with_fallbacks 无法正确触发 fallback。
    手动遍历模型列表，连接/认证错误时切换下一个模型。
    懒加载候选在运行时按需创建。
    """

    def __init__(
        self,
        structured_models: List[Tuple[str, str, Any]],
        creation_errors: List[str],
        lazy_candidates: Optional[List[Tuple[str, str]]] = None,
        schema: Any = None,
    ):
        self._models = structured_models  # [(provider, model_name, structured_runnable), ...]
        self._creation_errors = creation_errors
        self._lazy_candidates = lazy_candidates or []  # [(provider_id, model_name), ...]
        self._schema = schema

    def _resolve_lazy_candidate(self, provider_id: str, model_name: str) -> Optional[Tuple[str, str, Any]]:
        """按需创建懒加载候选的结构化模型"""
        try:
            fb_special_params = None
            if provider_id == "deepseek":
                fb_special_params = {"thinking": {"type": "disabled"}}

            fb_model = get_chat_model_by_provider(
                provider_id=provider_id,
                model_name=model_name,
                temperature=settings.openai_temperature,
                max_tokens=None,
                streaming=False,
                max_retries=0,
                special_params=fb_special_params,
            )
            fb_structured = fb_model.with_structured_output(self._schema)
            logger.info(f"懒加载 Fallback 结构化模型已创建: {provider_id}/{model_name}")
            return (provider_id, model_name, fb_structured)
        except Exception as e:
            logger.warning(f"懒加载 Fallback 结构化模型创建失败 {provider_id}/{model_name}: {e}")
            return None

    @staticmethod
    def _is_valid_result(result: Any) -> bool:
        """判断结构化输出是否有效

        防御性检查：流式 + with_structured_output 可能返回 None
        或者返回空对象（没有 Pydantic 字段填充）
        """
        if result is None:
            return False
        # Pydantic BaseModel 实例：检查是否有任何字段被填充
        if hasattr(result, "model_dump") and callable(result.model_dump):
            try:
                dumped = result.model_dump(exclude_none=False)
                if not dumped:
                    return False
                # 至少有一个字段包含"实质内容"：
                # - 非 None
                # - 非空字符串
                # - 非空列表/字典
                # - 非零数值
                def _has_meaningful_value(v: Any) -> bool:
                    if v is None:
                        return False
                    if isinstance(v, str):
                        return bool(v.strip())
                    if isinstance(v, (list, dict, tuple, set)):
                        return len(v) > 0
                    if isinstance(v, bool):
                        return v  # True 才算有意义
                    if isinstance(v, (int, float)):
                        return v != 0
                    # 其他类型：非空即可
                    return True

                has_value = any(_has_meaningful_value(v) for v in dumped.values())
                return has_value
            except Exception:
                return True  # 检查失败时保守认为有效
        # 字典/列表
        if isinstance(result, (dict, list)):
            return bool(result)
        # 字符串
        if isinstance(result, str):
            return bool(result.strip())
        return True

    @staticmethod
    def _sanitize_parsed_kwargs(result: Any) -> None:
        """将 AIMessage.additional_kwargs["parsed"] 中的 Pydantic 对象转为 dict

        langchain_openai 在解析 OpenAI structured output 响应时，会将
        Pydantic BaseModel 实例存入 ChatGeneration.message.additional_kwargs["parsed"]。
        LangChain tracer 在 on_llm_end 回调中调用 LLMResult.model_dump() 时，
        Pydantic v2 发现该字段类型推断为 None 但实际是 BaseModel 实例，
        触发 PydanticSerializationUnexpectedValue 警告。

        由于 with_structured_output 的 invoke 返回的是解析后的 Pydantic 对象
        （非 ChatGeneration），无法直接修改 tracer 处理的中间对象。
        因此在 invoke 期间抑制此特定警告，这是 LangChain 与 Pydantic v2
        的已知兼容性问题，不影响功能。
        """
        pass

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        """调用结构化模型，失败时自动切换（含懒加载候选）"""
        errors: List[str] = list(self._creation_errors)

        for provider, model_name, structured in self._models:
            try:
                result = structured.invoke(input, config=config, **kwargs)
                if not self._is_valid_result(result):
                    error_msg = f"{provider}/{model_name}: 返回空结果(None/空对象)"
                    errors.append(error_msg)
                    logger.warning(f"结构化模型返回空结果 {provider}/{model_name}，尝试下一个模型")
                    continue
                logger.info(f"结构化模型调用成功: {provider}/{model_name}")
                return result
            except Exception as e:
                errors.append(f"{provider}/{model_name}: {type(e).__name__}: {e}")
                logger.warning(f"结构化模型调用失败 {provider}/{model_name}: {type(e).__name__}: {e}")
                continue

        # 预实例化模型都失败，尝试懒加载候选
        for pid, mname in self._lazy_candidates:
            lazy_result = self._resolve_lazy_candidate(pid, mname)
            if lazy_result is not None:
                provider, model_name, structured = lazy_result
                try:
                    result = structured.invoke(input, config=config, **kwargs)
                    if not self._is_valid_result(result):
                        errors.append(f"{provider}/{model_name}: 返回空结果(None/空对象)")
                        logger.warning(f"懒加载结构化模型返回空结果 {provider}/{model_name}，尝试下一个")
                        continue
                    logger.info(f"懒加载结构化模型调用成功: {provider}/{model_name}")
                    return result
                except Exception as e:
                    errors.append(f"{provider}/{model_name}: {type(e).__name__}: {e}")
                    logger.warning(f"懒加载结构化模型调用失败 {provider}/{model_name}: {type(e).__name__}: {e}")
                    continue

        error_detail = "; ".join(errors)
        logger.error(f"所有结构化模型调用均失败: {error_detail}")
        raise RuntimeError(
            f"模型连接超时，所有已配置的模型均不可用。"
            f"已尝试: {error_detail}。请检查 API Key 配置和网络连接。"
        )

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        """异步调用结构化模型，失败时自动切换（含懒加载候选）"""
        errors: List[str] = list(self._creation_errors)

        for provider, model_name, structured in self._models:
            try:
                result = await structured.ainvoke(input, config=config, **kwargs)
                if not self._is_valid_result(result):
                    error_msg = f"{provider}/{model_name}: 返回空结果(None/空对象)"
                    errors.append(error_msg)
                    logger.warning(f"结构化模型异步返回空结果 {provider}/{model_name}，尝试下一个模型")
                    continue
                logger.info(f"结构化模型异步调用成功: {provider}/{model_name}")
                return result
            except Exception as e:
                errors.append(f"{provider}/{model_name}: {type(e).__name__}: {e}")
                logger.warning(f"结构化模型异步调用失败 {provider}/{model_name}: {type(e).__name__}: {e}")
                continue

        # 预实例化模型都失败，尝试懒加载候选
        for pid, mname in self._lazy_candidates:
            lazy_result = self._resolve_lazy_candidate(pid, mname)
            if lazy_result is not None:
                provider, model_name, structured = lazy_result
                try:
                    result = await structured.ainvoke(input, config=config, **kwargs)
                    if not self._is_valid_result(result):
                        errors.append(f"{provider}/{model_name}: 返回空结果(None/空对象)")
                        logger.warning(f"懒加载结构化模型异步返回空结果 {provider}/{model_name}，尝试下一个")
                        continue
                    logger.info(f"懒加载结构化模型异步调用成功: {provider}/{model_name}")
                    return result
                except Exception as e:
                    errors.append(f"{provider}/{model_name}: {type(e).__name__}: {e}")
                    logger.warning(f"懒加载结构化模型异步调用失败 {provider}/{model_name}: {type(e).__name__}: {e}")
                    continue

        error_detail = "; ".join(errors)
        logger.error(f"所有结构化模型异步调用均失败: {error_detail}")
        raise RuntimeError(
            f"模型连接超时，所有已配置的模型均不可用。"
            f"已尝试: {error_detail}。请检查 API Key 配置和网络连接。"
        )


# ==================== 运行时 Fallback 检测 ====================

class FallbackDetectionCallback(BaseCallbackHandler):
    """检测 LLM 运行时 fallback 的回调处理器

    当 LangChain 的 with_fallbacks() 在运行时切换到备选模型时，
    通过 on_llm_start/on_llm_end 回调检测实际使用的模型。

    继承 BaseCallbackHandler 以确保与 LangChain 回调管理器兼容
    （需要 run_inline 等属性）。

    用法:
        with FallbackDetectionCallback(expected_provider="ollama", expected_model="qwen3:8b") as fb:
            config["callbacks"] = [fb]
            # ... 执行 agent ...
        if fb.fallback_detected:
            # 发送降级提示
    """

    def __init__(self, expected_provider: str = "", expected_model: str = ""):
        super().__init__()
        self.expected_provider = expected_provider
        self.expected_model = expected_model
        self._llm_attempts: List[Dict[str, Any]] = []
        self._successful_model: Optional[str] = None
        self._successful_provider: Optional[str] = None
        self._fallback_detected = False
        self._first_success = True

    @property
    def fallback_detected(self) -> bool:
        return self._fallback_detected

    @property
    def actual_provider(self) -> Optional[str]:
        return self._successful_provider

    @property
    def actual_model(self) -> Optional[str]:
        return self._successful_model

    def get_fallback_info(self) -> Optional[Dict[str, str]]:
        """如果检测到降级，返回降级信息；否则返回 None"""
        if not self._fallback_detected:
            return None
        return {
            "original_provider": self.expected_provider,
            "original_model": self.expected_model,
            "actual_provider": self._successful_provider or "",
            "actual_model": self._successful_model or "",
            "message": (
                f"模型 {self.expected_provider}/{self.expected_model} 运行时失败，"
                f"已自动切换到 {self._successful_provider}/{self._successful_model}"
            ),
        }

    def on_llm_start(
        self,
        serialized: Dict[str, Any],
        prompts: List[str],
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        model_name = self._extract_model(serialized, **kwargs)
        self._llm_attempts.append({
            "run_id": str(run_id),
            "model": model_name,
            "success": False,
        })

    def on_llm_end(self, response: Any, *, run_id: Any = None, **kwargs: Any) -> None:
        rid = str(run_id)
        for attempt in self._llm_attempts:
            if attempt["run_id"] == rid:
                attempt["success"] = True
                break

        # 第一次成功的 LLM 调用就是实际使用的模型
        if self._first_success:
            for attempt in self._llm_attempts:
                if attempt["run_id"] == rid and attempt["success"]:
                    actual_model = attempt["model"]
                    # 从模型名推断 provider
                    actual_provider = self._infer_provider(actual_model)
                    self._successful_model = actual_model
                    self._successful_provider = actual_provider

                    # 检测是否降级
                    if (self.expected_provider and actual_provider and
                            actual_provider != self.expected_provider):
                        self._fallback_detected = True
                        logger.info(
                            f"LLM 运行时 fallback 检测: "
                            f"{self.expected_provider}/{self.expected_model} -> "
                            f"{actual_provider}/{actual_model}"
                        )
                    self._first_success = False
                    break

    def on_llm_error(self, error: Any, *, run_id: Any = None, **kwargs: Any) -> None:
        rid = str(run_id)
        for attempt in self._llm_attempts:
            if attempt["run_id"] == rid:
                attempt["success"] = False
                attempt["error"] = str(error)
                break

    def _extract_model(self, serialized: Dict[str, Any], **kwargs: Any) -> str:
        if "kwargs" in serialized:
            kw = serialized["kwargs"]
            for key in ("model", "model_name"):
                if key in kw:
                    return kw[key]
        if "name" in serialized:
            return serialized["name"]
        invocation_params = kwargs.get("invocation_params", {})
        for key in ("model_name", "model"):
            if key in invocation_params:
                return invocation_params[key]
        return ""

    def _infer_provider(self, model_name: str) -> str:
        """从模型名推断 provider"""
        if not model_name:
            return ""
        for pid, cfg in get_model_registry().items():
            for m in cfg.get("models", []):
                if isinstance(m, dict) and m.get("name") == model_name:
                    return pid
                elif isinstance(m, str) and m == model_name:
                    return pid
            if cfg.get("default_model") == model_name:
                return pid
        return ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False
