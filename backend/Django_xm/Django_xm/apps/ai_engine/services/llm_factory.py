"""
LLM 模型封装模块
提供统一的 LLM 模型接口，支持 OpenAI 等多种提供商

使用 LangChain v1.2+ 的 init_chat_model 统一模型初始化，
替代手动 ChatOpenAI 实例化，实现单一真相源。

改进：
1. 集成 InMemoryRateLimiter 防止 API 过载
2. 支持多模型提供商（OpenAI/Anthropic 等）

参考：
- https://docs.langchain.com/oss/python/langchain/models
- https://reference.langchain.com/python/langchain/chat_models/#init_chat_model
"""

from typing import Optional, Dict, Any, Union, Tuple

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from ..config import settings, get_logger, MODEL_REGISTRY
from .model_cache import make_cache_key, get_cached_model, set_cached_model

logger = get_logger(__name__)


_rate_limiter: Optional[Any] = None
_llm_cache: Optional[Any] = None
_deepseek_patch_applied: bool = False


def _apply_deepseek_reasoning_patch() -> None:
    """
    Monkey-patch langchain_openai 的两个关键函数

    DeepSeek 思考模式在流式响应的 delta 中返回 reasoning_content 字段，
    但 langchain-openai v1.0.2 存在两个缺陷：
    1. _convert_delta_to_message_chunk 未将 reasoning_content 保留到 additional_kwargs
    2. _convert_message_to_dict 未将 additional_kwargs 中的 reasoning_content 序列化回 API 格式

    导致 DeepSeek API 报 400: "reasoning_content must be passed back to the API"

    此 patch 修复两个环节，使 LangGraph Agent 在内部消息循环中正确回传 reasoning_content。
    """
    global _deepseek_patch_applied
    if _deepseek_patch_applied:
        return

    try:
        from langchain_openai.chat_models.base import (
            _convert_delta_to_message_chunk as _original_chunk_convert,
            _convert_message_to_dict as _original_msg_convert,
            AIMessageChunk,
            AIMessage,
        )

        _chunk_ref = _original_chunk_convert
        _msg_ref = _original_msg_convert

        def _convert_delta_with_reasoning(_dict, default_class):
            result = _chunk_ref(_dict, default_class)
            if isinstance(result, AIMessageChunk):
                rc = _dict.get("reasoning_content")
                if rc and isinstance(rc, str):
                    existing = getattr(result, "additional_kwargs", {}) or {}
                    existing["reasoning_content"] = rc
                    result.additional_kwargs = existing
            return result

        def _convert_msg_with_reasoning(message, api="chat/completions"):
            result = _msg_ref(message, api=api)
            if isinstance(message, AIMessage):
                rc = getattr(message, "additional_kwargs", {}).get("reasoning_content")
                if rc and isinstance(rc, str) and rc.strip():
                    result["reasoning_content"] = rc
            return result

        import langchain_openai.chat_models.base as _base_module
        _base_module._convert_delta_to_message_chunk = _convert_delta_with_reasoning
        _base_module._convert_message_to_dict = _convert_msg_with_reasoning
        _deepseek_patch_applied = True
        logger.info("DeepSeek reasoning_content patch 已应用（langchain_openai 流式修复）")
    except Exception as e:
        logger.warning(f"DeepSeek reasoning_content patch 应用失败: {e}")


_apply_deepseek_reasoning_patch()


def get_llm_cache() -> Any:
    """
    获取全局 LLM 缓存实例

    使用 langchain_core.caches.InMemoryCache 缓存 LLM 响应，
    避免相同 prompt 重复调用 API，降低成本和延迟。

    生产环境可替换为 RedisSemanticCache 实现语义缓存。

    Returns:
        InMemoryCache 实例
    """
    global _llm_cache
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
    """
    设置全局 LLM 缓存

    使用 langchain_core.globals.set_llm_cache 绑定缓存实例，
    使所有 LLM 调用自动受益于缓存。

    LangChain v1.2+ 推荐使用 globals 模块设置全局缓存，
    而非直接赋值 langchain_core.llm_cache。
    """
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
    """
    设置语义缓存（需要 Redis 和 Embedding 模型）

    语义缓存基于向量相似度匹配，即使 prompt 措辞不同，
    只要语义相近就能命中缓存。

    Args:
        redis_url: Redis 连接 URL
        embedding_model: Embedding 模型名称
    """
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
    """
    获取全局速率限制器实例

    使用 langchain_core.rate_limiters.InMemoryRateLimiter，
    防止在短时间内发送过多请求导致 API 限流。

    Returns:
        InMemoryRateLimiter 实例
    """
    global _rate_limiter
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


def get_chat_model(
    model_name: Optional[str] = None,
    model_provider: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    streaming: Optional[bool] = None,
    **kwargs: Any,
) -> BaseChatModel:
    """
    获取配置好的聊天模型实例

    使用 init_chat_model 统一初始化，支持自动提供商推断。
    如 model_name="gpt-4o" 自动推断为 openai 提供商。

    Args:
        model_name: 模型名称，默认使用配置中的 openai_model
        model_provider: 模型提供商，默认为 "openai"
        temperature: 温度参数 (0.0-2.0)
        max_tokens: 最大生成 token 数
        streaming: 是否启用流式输出
        **kwargs: 其他传递给模型的参数

    Returns:
        配置好的 ChatModel 实例
    """
    model_name = model_name or settings.openai_model
    provider = model_provider or "openai"
    temperature = temperature if temperature is not None else settings.openai_temperature
    streaming = streaming if streaming is not None else settings.openai_streaming

    init_kwargs: Dict[str, Any] = {
        "model": model_name,
        "model_provider": provider,
        "temperature": temperature,
        "streaming": streaming,
        "timeout": 120.0,
        "max_retries": 3,
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

    cache_key = make_cache_key(model_name, provider, temperature, streaming, init_kwargs.get("max_tokens"))

    cached = get_cached_model(cache_key)
    if cached is not None:
        return cached

    try:
        model = init_chat_model(**init_kwargs)
        logger.debug(f"模型创建成功: {model_name}")

        set_cached_model(cache_key, model)

        return model
    except Exception as e:
        logger.error(f"模型创建失败: {e}")
        raise


def get_streaming_model(
    model_name: Optional[str] = None,
    model_provider: Optional[str] = None,
    temperature: Optional[float] = None,
    **kwargs: Any,
) -> BaseChatModel:
    """
    获取启用流式输出的聊天模型

    这是 get_chat_model 的便捷包装，强制启用流式输出。
    """
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
    """
    获取用于结构化输出的聊天模型

    结构化输出通常需要更低的温度以确保输出格式的一致性。
    可选传入 Pydantic 模型类，自动绑定 with_structured_output。

    Args:
        model_name: 模型名称
        model_provider: 模型提供商
        temperature: 温度参数，默认 0.0
        response_format: Pydantic 模型类或 JSON Schema，用于结构化输出
        **kwargs: 其他参数

    Returns:
        配置好的 ChatModel 实例（可能已绑定结构化输出）
    """
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


MODEL_CONFIGS = {
    "default": {
        "model_name": "gpt-4o",
        "model_provider": "openai",
        "temperature": 0.7,
        "description": "默认模型，平衡性能和成本",
    },
    "fast": {
        "model_name": "gpt-4o-mini",
        "model_provider": "openai",
        "temperature": 0.7,
        "description": "快速模型，适合简单任务",
    },
    "precise": {
        "model_name": "gpt-4o",
        "model_provider": "openai",
        "temperature": 0.3,
        "description": "精确模型，适合需要准确性的任务",
    },
    "creative": {
        "model_name": "gpt-4o",
        "model_provider": "openai",
        "temperature": 1.0,
        "description": "创意模型，适合需要创造性的任务",
    },
    "anthropic_default": {
        "model_name": "claude-sonnet-4-20250514",
        "model_provider": "anthropic",
        "temperature": 0.7,
        "description": "Anthropic Claude Sonnet 4，平衡性能和成本",
    },
    "anthropic_fast": {
        "model_name": "claude-3-5-haiku-20241022",
        "model_provider": "anthropic",
        "temperature": 0.7,
        "description": "Anthropic Claude Haiku，快速响应",
    },
    "anthropic_precise": {
        "model_name": "claude-sonnet-4-20250514",
        "model_provider": "anthropic",
        "temperature": 0.3,
        "description": "Anthropic Claude Sonnet 4，精确模式",
    },
}


def _get_provider_config(provider: str) -> Dict[str, Any]:
    """
    获取提供商特定的配置（API key、base_url 等）

    基于 MODEL_REGISTRY 动态查找，所有 API Key
    统一通过 Django settings 管理，确保单一真相源。

    Args:
        provider: 提供商名称（openai/deepseek/groq/anthropic/baidu_qianfan）

    Returns:
        提供商配置字典
    """
    for _key, cfg in MODEL_REGISTRY.items():
        if cfg["provider"] == provider or _key == provider:
            result: Dict[str, Any] = {}
            api_key = getattr(settings, cfg["api_key_attr"], "")
            if api_key:
                result["api_key"] = api_key
            if cfg.get("base_url_attr"):
                base_url = getattr(settings, cfg["base_url_attr"], "")
                if base_url:
                    result["base_url"] = base_url
            return result

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
    """
    根据预设配置获取模型

    Args:
        preset: 预设名称，可选值: default, fast, precise, creative
        **kwargs: 覆盖预设的参数

    Returns:
        配置好的 ChatModel 实例

    Raises:
        ValueError: 如果预设名称不存在
    """
    if preset not in MODEL_CONFIGS:
        available = ", ".join(MODEL_CONFIGS.keys())
        raise ValueError(f"未知的预设: {preset}. 可用预设: {available}")

    config = MODEL_CONFIGS[preset].copy()
    config.pop("description", None)
    model_provider = config.pop("model_provider", None)
    config.update(kwargs)

    logger.info(f"使用预设模型配置: {preset}")
    return get_chat_model(model_provider=model_provider, **config)


def get_model_string(
    model_name: Optional[str] = None,
    provider: str = "openai",
) -> str:
    """
    获取模型标识符字符串

    在 LangChain v1.0+ 中，create_agent 接受字符串格式的模型标识符，
    如 "openai:gpt-4o"、"anthropic:claude-3-5-sonnet-20241022" 等。

    Args:
        model_name: 模型名称，如果为 None 则使用配置中的默认模型
        provider: 提供商名称，默认为 "openai"

    Returns:
        模型标识符字符串，格式为 "provider:model_name"
    """
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
    """
    按 provider_id 从 MODEL_REGISTRY 获取模型实例

    这是面向前端模型选择的核心方法，根据 provider_id
    从注册表查找配置，自动注入 API key 和 base_url。

    Args:
        provider_id: 提供商 ID（openai/deepseek/groq/baidu_qianfan/anthropic）
        model_name: 模型名称，默认使用注册表中的 default_model
        temperature: 温度参数
        max_tokens: 最大 token 数
        streaming: 是否流式
        special_params: 提供商专属参数，如 DeepSeek 的 thinking/reasoning_effort
        **kwargs: 其他参数

    Returns:
        配置好的 ChatModel 实例
    """
    registry = MODEL_REGISTRY.get(provider_id)
    if registry is None:
        available = ", ".join(MODEL_REGISTRY.keys())
        raise ValueError(f"未知的提供商: {provider_id}. 可用: {available}")

    api_key = getattr(settings, registry["api_key_attr"], "")
    if not api_key or not api_key.strip():
        raise ValueError(f"提供商 {registry['label']} 的 API Key 未配置")

    resolved_model = model_name or registry["default_model"]
    provider = registry["provider"]
    resolved_temp = temperature if temperature is not None else settings.openai_temperature
    resolved_streaming = streaming if streaming is not None else settings.openai_streaming

    init_kwargs: Dict[str, Any] = {
        "model": resolved_model,
        "model_provider": provider,
        "temperature": resolved_temp,
        "streaming": resolved_streaming,
        "timeout": 120.0,
        "max_retries": 3,
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
        if model_kwargs:
            init_kwargs["model_kwargs"] = model_kwargs
        if extra_body:
            init_kwargs["extra_body"] = extra_body

        thinking_enabled = False
        thinking_cfg = registry.get("special_params", {}).get("thinking")
        if thinking_cfg and special_params.get("thinking"):
            enabled_val = thinking_cfg.get("enabled_value", {})
            if special_params["thinking"] == enabled_val:
                thinking_enabled = True
        if thinking_enabled:
            init_kwargs.pop("temperature", None)
            init_kwargs.pop("top_p", None)
            logger.debug("DeepSeek 思考模式已启用，移除 temperature/top_p 参数")

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
    )

    if use_cache:
        cached = get_cached_model(cache_key)
        if cached is not None:
            return cached

    try:
        model = init_chat_model(**init_kwargs)
        model._provider_id = provider
        logger.debug(f"模型创建成功: {resolved_model}")

        if provider == "groq" and hasattr(model, "bind_tools"):
            original_bind = model.bind_tools

            def _groq_bind_tools(tools, *, tool_choice=None, **kw):
                kw.setdefault("parallel_tool_calls", False)
                return original_bind(tools, tool_choice=tool_choice, **kw)

            model.bind_tools = _groq_bind_tools
            logger.debug("Groq 模型已注入 parallel_tool_calls=False")

        if use_cache:
            set_cached_model(cache_key, model)

        return model
    except Exception as e:
        logger.error(f"模型创建失败 (provider_id={provider_id}): {e}")
        raise


def test_model_connection(
    provider_id: str,
    model_name: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    测试模型连接是否正常

    Args:
        provider_id: 提供商 ID
        model_name: 模型名称
        **kwargs: 其他参数

    Returns:
        {"success": bool, "message": str, "model_info": dict}
    """
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

        return {
            "success": True,
            "message": f"模型连接成功",
            "model_info": {
                "provider_id": provider_id,
                "model_name": model_name or MODEL_REGISTRY[provider_id]["default_model"],
                "response_preview": str(response.content)[:100],
            },
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"模型连接失败: {str(e)}",
            "model_info": {
                "provider_id": provider_id,
                "model_name": model_name or MODEL_REGISTRY.get(provider_id, {}).get("default_model", ""),
            },
        }
