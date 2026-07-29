"""LLM 模型工厂（公开 API 层）

提供统一的 LLM 模型创建接口，支持 OpenAI / Anthropic / Ollama / DeepSeek 等多种 provider。

使用 LangChain v1.2+ 的 init_chat_model 统一模型初始化，
替代手动 ChatOpenAI 实例化，实现单一真相源。

模块拆分（Task 19）：
- **llm_cache.py**: 缓存与速率限制基础设施（InMemoryCache / RedisSemanticCache / RateLimiter）
- **llm_fallback.py**: Fallback 机制（LazyFallbackChatModel / StructuredModelWithFallback /
  FallbackDetectionCallback / get_fallback_candidates）
- **llm_factory.py**（本文件）: 公开 API 层（get_chat_model / get_chat_model_by_provider /
  get_helper_model / get_structured_model_with_fallback / JsonModeStructuredModel 等）

依赖关系（DAG，无循环）：
  llm_cache ← llm_fallback ← llm_factory

参考：
- https://docs.langchain.com/oss/python/langchain/models
- https://reference.langchain.com/python/langchain/chat_models/#init_chat_model
"""

from typing import Any

from django.conf import settings as django_settings
from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig

from Django_xm.apps.core.config import get_logger

from ..config import HELPER_MODEL_PRIORITY, get_model_presets, settings
from ..providers import (
    PROVIDER_REGISTRY,
    apply_reasoning_patch_if_needed,
    is_thinking_enabled,
    patch_groq_model,
)

# 从拆分后的模块导入（Task 19）
from .llm_cache import (
    cached_model_creation,
    get_rate_limiter,
)
from .llm_fallback import (
    LazyFallbackChatModel,
    StructuredModelWithFallback,
    get_fallback_candidates,
)
from .model_cache import make_cache_key
from .registry_service import (
    get_model_registry,
    get_provider_config,
)
from .registry_service import (
    is_provider_available as registry_is_provider_available,
)

logger = get_logger(__name__)


# ============== 模型能力查询 ==============


def model_supports_capability(provider_id: str, model_name: str, capability: str) -> bool:
    """检查模型是否支持指定能力（如 deep_thinking / vision / tool_calling）"""
    provider_cfg = get_provider_config(provider_id)
    for model_cfg in provider_cfg.get("models", []):
        if isinstance(model_cfg, dict) and model_cfg.get("name") == model_name:
            return capability in model_cfg.get("capabilities", [])
        elif isinstance(model_cfg, str) and model_cfg == model_name:
            return False  # 旧格式兼容
    return False


# ============== 内部辅助：special_params 解析 ==============


def _apply_special_params(
    init_kwargs: dict[str, Any],
    special_params: dict[str, Any],
    provider_id: str,
    provider: str,
    model_name: str,
) -> None:
    """解析 special_params 并应用到 init_kwargs

    将 special_params 按 provider 注册表中的规则拆解为
    model_kwargs / extra_body / top_level 参数，而非原样透传给 init_chat_model。
    此函数被 _create_single_chat_model 和 get_chat_model_by_provider 共用。
    """
    registry = PROVIDER_REGISTRY.get(provider_id) or get_provider_config(provider_id)
    if not registry:
        logger.debug(f"special_params: 未找到 provider {provider_id} 的注册信息，忽略 special_params")
        return

    model_kwargs: dict[str, Any] = {}
    extra_body: dict[str, Any] = {}
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
    if "reasoning_effort" in special_params and "thinking" not in special_params:
        thinking_cfg = registry.get("special_params", {}).get("thinking")
        if thinking_cfg:
            # 注意：不修改传入的 special_params 字典，只移除 init_kwargs 中已注入的值
            init_kwargs.pop("reasoning_effort", None)
            extra_body.pop("reasoning_effort", None)
            model_kwargs.pop("reasoning_effort", None)
            logger.debug("reasoning_effort 已设置但 thinking 未启用，移除 reasoning_effort")

    if model_kwargs:
        init_kwargs["model_kwargs"] = model_kwargs
    if extra_body:
        init_kwargs["extra_body"] = extra_body

    # 深度思考模式：Provider 感知参数注入
    if model_supports_capability(provider_id, model_name, "deep_thinking") and is_thinking_enabled(
        special_params, provider_id
    ):
        if provider_id == "deepseek":
            init_kwargs.pop("temperature", None)
            init_kwargs.pop("top_p", None)
            logger.debug("DeepSeek 深度思考模式已启用，移除 temperature/top_p 参数")
        if provider == "ollama" and "reasoning" not in init_kwargs:
            init_kwargs["reasoning"] = True
            logger.debug("Ollama 深度思考模式已启用，注入 reasoning=True")
        if provider == "anthropic" and "thinking" not in init_kwargs:
            init_kwargs["thinking"] = {"type": "enabled", "budget_tokens": 10000}
            logger.debug("Anthropic 扩展思考模式已启用，注入 thinking 参数")


# ============== 单模型创建 ==============


def _create_single_chat_model(
    model_name: str | None = None,
    model_provider: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    streaming: bool | None = None,
    use_cache: bool = True,
    **kwargs: Any,
) -> BaseChatModel:
    """创建单个聊天模型（无 fallback）

    内部函数，供 get_chat_model() 共用。
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
    provider = model_provider or getattr(django_settings, "AI_DEFAULT_PROVIDER", "openai")
    temperature = temperature if temperature is not None else settings.openai_temperature
    streaming = streaming if streaming is not None else settings.openai_streaming

    init_kwargs: dict[str, Any] = {
        "model": model_name,
        "model_provider": provider,
        "temperature": temperature,
        "streaming": streaming,
        "timeout": getattr(django_settings, "AI_LLM_TIMEOUT", 120.0),
        "max_retries": getattr(django_settings, "AI_LLM_MAX_RETRIES", 3),
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

    # 从 kwargs 中提取 special_params，使用 provider 感知逻辑解析
    # 避免原样透传给 init_chat_model 导致 API 报错
    special_params = kwargs.pop("special_params", None)
    if special_params:
        # 确定 provider_id 用于查找注册表
        provider_id: str = kwargs.pop("provider_id", None) or provider
        _apply_special_params(init_kwargs, special_params, provider_id, provider, model_name)

        if model_supports_capability(provider_id, model_name, "deep_thinking"):
            apply_reasoning_patch_if_needed()

    init_kwargs.update(kwargs)

    logger.info(
        f"创建聊天模型: {model_name} "
        f"(provider={init_kwargs['model_provider']}, "
        f"temperature={temperature}, streaming={streaming})"
    )

    cache_key = make_cache_key(
        model_name,
        provider,
        temperature,
        streaming,
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

    return cached_model_creation(cache_key, use_cache, _create)


# ============== 公开 API：模型创建 ==============


def get_chat_model(
    model_name: str | None = None,
    model_provider: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    streaming: bool | None = None,
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

    resolved_provider: str = model_provider or getattr(django_settings, "AI_DEFAULT_PROVIDER", "openai")
    resolved_model_name: str = model_name or settings.openai_model

    # 1. 尝试创建主模型（max_retries=0 快速失败，由 fallback 接管）
    primary_model = None
    creation_errors: list[str] = []

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
                f"模型连接超时，所有已配置的模型均不可用。已尝试: {error_detail}。请检查 API Key 配置和网络连接。"
            )

    # 4. 使用 LazyFallbackChatModel 包装（fallback 模型延迟到运行时按需创建）
    #    factory=get_chat_model_by_provider 通过依赖注入避免循环导入
    if candidates:
        lazy_model = LazyFallbackChatModel(
            primary=primary_model,
            fallback_candidates=candidates,
            factory=get_chat_model_by_provider,
            temperature=temperature if temperature is not None else settings.openai_temperature,
            max_tokens=max_tokens,
            streaming=streaming if streaming is not None else settings.openai_streaming,
        )
        logger.info(f"已配置模型 fallback（懒加载）: 主模型 + {len(candidates)} 个候选")
        return lazy_model

    logger.warning("无可用 fallback 模型，仅使用主模型（无自动切换）")
    return primary_model


def get_streaming_model(
    model_name: str | None = None,
    model_provider: str | None = None,
    temperature: float | None = None,
    **kwargs: Any,
) -> BaseChatModel:
    """创建流式模型（便捷封装，streaming=True）"""
    return get_chat_model(
        model_name=model_name,
        model_provider=model_provider,
        temperature=temperature,
        streaming=True,
        **kwargs,
    )


def get_structured_output_model(
    model_name: str | None = None,
    model_provider: str | None = None,
    temperature: float = 0.0,
    response_format: Any | None = None,
    **kwargs: Any,
) -> BaseChatModel:
    """创建结构化输出模型（with_structured_output 封装）"""
    model = get_chat_model(
        model_name=model_name,
        model_provider=model_provider,
        temperature=temperature,
        streaming=False,
        **kwargs,
    )

    if response_format is not None:
        try:
            base = model.bound if hasattr(model, "bound") else model
            model = base.with_structured_output(response_format)
            logger.info(f"已绑定结构化输出: {getattr(response_format, '__name__', str(response_format))}")
        except Exception as e:
            logger.warning(f"绑定结构化输出失败: {e}，将使用普通模式")

    return model


def get_model_config(preset: str) -> dict:
    """从统一配置获取模型预设配置"""
    presets = get_model_presets()
    return presets.get(preset, {})


def _get_provider_config(provider: str) -> dict[str, Any]:
    """从数据库获取 provider 的 API 配置（api_key, base_url）"""
    for provider_id, cfg in get_model_registry().items():
        if cfg["provider"] == provider or provider_id == provider:
            result: dict[str, Any] = {}
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
    """按预设创建模型（preset 来自 settings.MODEL_PRESETS）"""
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
    model_name: str | None = None,
    provider: str | None = None,
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

    # 2. 回退到 settings
    provider = provider or getattr(django_settings, "AI_DEFAULT_PROVIDER", "openai")
    model_name = model_name or settings.openai_model

    model_string = f"{provider}:{model_name}"
    logger.debug(f"生成模型标识符: {model_string}")
    return model_string


# ============== 按 provider_id 创建模型 ==============


def get_chat_model_by_provider(
    provider_id: str,
    model_name: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    streaming: bool | None = None,
    special_params: dict[str, Any] | None = None,
    **kwargs: Any,
) -> BaseChatModel:
    """按 provider_id 创建聊天模型

    与 get_chat_model 的差异：本函数不启用 fallback，直接按指定 provider 创建。
    用于 LazyFallbackChatModel 的 factory 参数（依赖注入）。
    """
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

    init_kwargs: dict[str, Any] = {
        "model": resolved_model,
        "model_provider": provider,
        "temperature": resolved_temp,
        "streaming": resolved_streaming,
        "timeout": getattr(django_settings, "AI_LLM_TIMEOUT", 120.0),
        "max_retries": getattr(django_settings, "AI_LLM_MAX_RETRIES", 3),
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
        _apply_special_params(init_kwargs, special_params, provider_id, provider, resolved_model)

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
        resolved_model,
        provider,
        resolved_temp,
        resolved_streaming,
        init_kwargs.get("max_tokens"),
        special_suffix,
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
                k: v
                for k, v in init_kwargs.items()
                if k
                not in (
                    "model",
                    "model_provider",
                    "temperature",
                    "streaming",
                    "api_key",
                    "rate_limiter",
                    "max_tokens",
                    "timeout",
                    "max_retries",
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

    return cached_model_creation(cache_key, use_cache, _create, error_context=f"provider_id={provider_id}")


# ============== Groq bind_tools 兼容补丁 ==============

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
        from pydantic import Field

        if "bind_tools" not in ChatGroq.model_fields:
            # 添加一个默认为 None 的字段以避免 init_chat_model 构造时
            # "object has no field bind_tools" 错误
            ChatGroq.model_fields["bind_tools"] = Field(default=None)  # type: ignore[assignment]

        # 同时给类添加一个真正的 bind_tools 方法实现
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


# ============== 模型连接测试 ==============


def test_model_connection(
    provider_id: str,
    model_name: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """测试模型连接（发送 "Hi" 消息验证可用性）"""
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
        from langchain_core.globals import get_llm_cache, set_llm_cache
        from langchain_core.messages import HumanMessage

        original_cache = get_llm_cache()
        try:
            set_llm_cache(None)
            response = model.invoke([HumanMessage(content="Hi")])
        finally:
            set_llm_cache(original_cache)

        registry = PROVIDER_REGISTRY.get(provider_id) or get_provider_config(provider_id)
        return {
            "success": True,
            "message": "模型连接成功",
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
            "message": f"模型连接失败: {e!s}",
            "model_info": {
                "provider_id": provider_id,
                "model_name": model_name or registry.get("default_model", ""),
            },
        }


# ============== 辅助模型 ==============

_helper_model_cache: BaseChatModel | None = None


def get_helper_model() -> BaseChatModel | None:
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
        # 配置读取失败时回退到运行时内存，不影响主流程
        logger.debug("读取 helper_model 配置失败，回退到运行时内存")

    # 2. 回退到运行时内存（兼容旧逻辑）
    if not helper_provider:
        helper_provider = getattr(django_settings, "AI_HELPER_MODEL_PROVIDER", "")
        helper_model_name = getattr(django_settings, "AI_HELPER_MODEL_NAME", "")
    helper_temp = getattr(django_settings, "AI_HELPER_MODEL_TEMPERATURE", 0.0)
    helper_max_tokens = getattr(django_settings, "AI_HELPER_MODEL_MAX_TOKENS", 256)

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


# ============== 数据库配置读取 ==============


def get_system_default_chat_model() -> dict[str, str] | None:
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
        # 配置读取失败时返回 None，使用默认 provider
        logger.debug("读取 default_chat_model 配置失败，返回 None")
    return None


# ============== 结构化输出 + Fallback ==============


def get_structured_model_with_fallback(
    schema: Any,
    model_name: str | None = None,
    model_provider: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    streaming: bool | None = False,  # 结构化输出默认禁用流式
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

    resolved_provider: str = model_provider or getattr(django_settings, "AI_DEFAULT_PROVIDER", "openai")
    resolved_model_name: str = model_name or settings.openai_model

    structured_models: list[tuple[str, str, Any]] = []
    creation_errors: list[str] = []

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
            f"模型连接超时，所有已配置的模型均不可用。已尝试: {error_detail}。请检查 API Key 配置和网络连接。"
        )

    logger.info(f"已配置结构化模型 fallback（懒加载）: 主模型 + {len(candidates)} 个候选")
    # factory=get_chat_model_by_provider 通过依赖注入避免 llm_fallback → llm_factory 循环导入
    return StructuredModelWithFallback(
        structured_models,
        creation_errors,
        lazy_candidates=candidates,
        schema=schema,
        factory=get_chat_model_by_provider,
    )


# ============== JSON Mode 结构化输出（DeepSeek 专用） ==============


class JsonModeStructuredModel:
    """JSON mode 结构化输出模型（DeepSeek 专用）

    DeepSeek thinking mode 与 tool_choice 冲突，无法使用 with_structured_output。
    本类通过 response_format={"type": "json_object"} + 手动注入 JSON Schema 提示词
    实现等效的结构化输出：

    1. 构造时接收已 bind(response_format={"type": "json_object"}) 的 model
    2. invoke/ainvoke 时在 messages 首位注入 SystemMessage（含 schema 提示词）
    3. 调用模型获取 JSON 字符串响应
    4. 解析 JSON 并用 Pydantic schema 校验，返回 BaseModel 实例
    5. 无效 JSON / 空内容 / schema 校验失败 → 返回 None（触发上层重试）
    6. 模型调用异常向上传播（由 ResilientModel + ResilientInvoker 统一处理）
    """

    def __init__(
        self,
        model: BaseChatModel,
        schema: Any,
        provider: str,
        model_name: str,
    ):
        self._model = model
        self._schema = schema
        self._provider = provider
        self._model_name = model_name

    def _build_schema_prompt(self) -> str:
        """构造 JSON Schema 提示词"""
        import json as _json

        try:
            schema_json = self._schema.model_json_schema()
        except Exception:
            schema_json = {}
        return (
            "请严格按照以下 JSON Schema 输出 JSON，不要输出任何其他内容。\n"
            f"JSON Schema:\n{_json.dumps(schema_json, ensure_ascii=False, indent=2)}"
        )

    def _build_messages(self, input_data: Any) -> list:
        """在输入消息前注入 JSON Schema SystemMessage"""
        if isinstance(input_data, list):
            messages = list(input_data)
        else:
            messages = [input_data]
        # schema 提示词注入到首位
        messages.insert(0, SystemMessage(content=self._build_schema_prompt()))
        return messages

    def _parse_result(self, content: str) -> Any | None:
        """解析模型返回的 JSON 内容为 Pydantic 实例

        Args:
            content: 模型返回的文本内容

        Returns:
            Pydantic BaseModel 实例，解析/校验失败返回 None
        """
        import json as _json
        import re as _re

        if not content or not content.strip():
            return None

        text = content.strip()

        # 去除 markdown 代码块包裹
        md_match = _re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, _re.DOTALL)
        if md_match:
            text = md_match.group(1).strip()

        # 尝试解析 JSON
        try:
            data = _json.loads(text)
        except (_json.JSONDecodeError, ValueError):
            return None

        if not isinstance(data, dict):
            return None

        # Pydantic schema 校验
        try:
            return self._schema(**data)
        except Exception:
            return None

    def invoke(self, input_data: Any, config: RunnableConfig | None = None) -> Any | None:
        """同步调用模型并解析 JSON 输出

        模型调用异常向上传播（不吞没），由 ResilientModel/ResilientInvoker 统一处理重试/降级。
        """
        messages = self._build_messages(input_data)
        response = self._model.invoke(messages, config=config)
        content = getattr(response, "content", "") or ""
        return self._parse_result(content)

    async def ainvoke(self, input_data: Any, config: RunnableConfig | None = None) -> Any | None:
        """异步调用模型并解析 JSON 输出"""
        messages = self._build_messages(input_data)
        response = await self._model.ainvoke(messages, config=config)
        content = getattr(response, "content", "") or ""
        return self._parse_result(content)
