"""统一模型层级管理

实现"主模型 → 降级模型"两级降级链架构，通过 ResilientModel 包装。
所有 agent 统一适用：主模型重试 3 次失败后切换到降级模型。

核心函数：
- get_default_model(): 返回默认模型 + 降级链
- get_helper_model(): 返回辅助模型 + 降级链
- get_structured_model(): 返回结构化输出模型 + 降级链
- get_streaming_model(): 返回流式模型 + 降级链

配置从 SystemConfig 读取：
- default_chat_model: 主 Agent 使用的模型
- helper_model: 子 Agent / 评分 / 判断使用的模型
- fallback_chat_model: 降级模型（主模型失败后切换）
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

# resilient_invoker 已从 ai_engine/services 迁移至 agent_hub/services，使用绝对路径导入
from Django_xm.apps.agent_hub.services.resilient_invoker import ResilientModel
from Django_xm.apps.core.config import get_logger

from .llm_factory import (
    get_chat_model_by_provider,
)

logger = get_logger(__name__)


# ============================================================================
# 辅助函数
# ============================================================================


def _read_model_config(config_key: str) -> tuple[str, str]:
    """从 SystemConfig 读取模型配置

    Args:
        config_key: SystemConfig 中的配置键
                    （"default_chat_model" / "helper_model" / "fallback_chat_model"）

    Returns:
        (provider_id, model_name)，未配置时返回 ("", "")
    """
    try:
        from Django_xm.apps.ai_engine.models import SystemConfig

        config = SystemConfig.get_value(config_key, {})
        if isinstance(config, dict):
            return config.get("provider_id", ""), config.get("model_name", "")
    except Exception as e:
        logger.warning(f"读取 {config_key} 配置失败: {e}")
    return "", ""


def _create_model_from_candidate(
    provider_id: str,
    model_name: str,
    temperature: float | None = None,
    max_tokens: int | None = None,
    streaming: bool | None = None,
    use_cache: bool = True,
    **kwargs: Any,
) -> BaseChatModel:
    """通过 provider_id + model_name 创建模型实例

    复用 llm_factory.get_chat_model_by_provider 的模型创建逻辑
    （它内部调用 init_chat_model + _cached_model_creation，与
    _create_single_chat_model 等价，但能正确处理 provider_id 到
    langchain provider 类型的转换）。

    Args:
        provider_id: 提供商 ID（如 "openai", "deepseek"）
        model_name: 模型名称
        temperature: 温度参数
        max_tokens: 最大 token 数
        streaming: 是否流式
        use_cache: 是否使用缓存
        **kwargs: 其他参数（透传给 get_chat_model_by_provider）

    Returns:
        BaseChatModel 实例

    Raises:
        Exception: 模型创建失败时透传原始异常
    """
    return get_chat_model_by_provider(
        provider_id=provider_id,
        model_name=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        streaming=streaming,
        use_cache=use_cache,
        **kwargs,
    )


# ============================================================================
# 主函数
# ============================================================================


def _build_resilient_chain(
    primary_config_key: str,
    role_label: str,
    temperature: float | None = None,
    max_tokens: int | None = None,
    streaming: bool | None = None,
    use_cache: bool = True,
    enable_fallback: bool = True,
    model_name: str | None = None,
    model_provider: str | None = None,
    **kwargs: Any,
) -> BaseChatModel:
    """统一降级链构建：主模型 + 降级模型

    1. 读取主模型配置并创建实例（创建失败直接抛 RuntimeError，快速失败）
    2. 不启用降级链时返回主模型
    3. 读取降级模型配置并创建实例（创建失败仅记日志，不阻塞主模型使用）
    4. ResilientModel(models=[主模型, 降级模型]) 包装

    运行时降级（重试3次后切换）由 ResilientInvoker 保证，此处不处理。

    Args:
        primary_config_key: SystemConfig 中的配置键（"default_chat_model" 或 "helper_model"）
        role_label: 日志中的角色标签（"默认" 或 "辅助"）
        temperature: 温度参数
        max_tokens: 最大 token 数
        streaming: 是否流式
        use_cache: 是否使用缓存
        enable_fallback: 是否启用降级链
        model_name: 覆盖 SystemConfig 的模型名称（仅 default_chat_model 支持）
        model_provider: 覆盖 SystemConfig 的提供商（仅 default_chat_model 支持）
    """
    # 1. 解析主模型配置（优先使用显式参数，其次 SystemConfig）
    if not model_provider or not model_name:
        cfg_provider, cfg_model = _read_model_config(primary_config_key)
        model_provider = model_provider or cfg_provider
        model_name = model_name or cfg_model

    if not model_provider or not model_name:
        raise RuntimeError(
            f"未配置{role_label}模型，请在系统设置中配置 {primary_config_key}"
        )

    # 2. 创建主模型（失败包装为 RuntimeError 快速抛出，与"未配置"分支保持一致）
    try:
        primary_model = _create_model_from_candidate(
            provider_id=model_provider,
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            streaming=streaming,
            use_cache=use_cache,
            **kwargs,
        )
    except Exception as e:
        raise RuntimeError(
            f"{role_label}模型创建失败 ({model_provider}/{model_name}): {e}"
        ) from e
    logger.info(f"{role_label}模型(用户配置): {model_provider}/{model_name}")

    # 3. 不启用降级链时直接返回主模型
    if not enable_fallback:
        return primary_model

    # 4. 读取并创建降级模型（失败仅记日志，不阻塞主模型使用）
    fb_provider, fb_model = _read_model_config("fallback_chat_model")
    if not fb_provider or not fb_model:
        logger.warning(f"未配置降级模型，仅使用{role_label}模型（无降级链）")
        return primary_model

    try:
        fallback_model = _create_model_from_candidate(
            provider_id=fb_provider,
            model_name=fb_model,
            temperature=temperature,
            max_tokens=max_tokens,
            streaming=streaming,
            use_cache=use_cache,
            **kwargs,
        )
    except Exception as e:
        logger.warning(
            f"降级模型创建失败: {fb_provider}/{fb_model}: {e}，仅使用{role_label}模型"
        )
        return primary_model

    # 5. ResilientModel 包装（运行时重试3次 + 切换降级模型 由 ResilientInvoker 保证）
    logger.info(f"已配置{role_label}模型 + 降级链: 2 个模型")
    return ResilientModel(models=[primary_model, fallback_model])


def get_default_model(
    model_name: str | None = None,
    model_provider: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    streaming: bool | None = None,
    use_cache: bool = True,
    enable_fallback: bool = True,
    **kwargs: Any,
) -> BaseChatModel:
    """返回默认模型 + 降级链

    降级链：[默认模型, 降级模型] — 默认模型重试3次失败后切换到降级模型。
    降级模型来自 SystemConfig 的 fallback_chat_model 配置。

    Args:
        model_name: 模型名称（覆盖 SystemConfig）
        model_provider: 模型提供商（覆盖 SystemConfig）
        temperature: 温度参数
        max_tokens: 最大 token 数
        streaming: 是否流式
        use_cache: 是否使用缓存
        enable_fallback: 是否启用降级链，默认 True
        **kwargs: 其他参数

    Returns:
        enable_fallback=True 时返回 ResilientModel 实例；
        enable_fallback=False 时返回原始 BaseChatModel 实例

    Raises:
        RuntimeError: 所有模型都不可用时
    """
    return _build_resilient_chain(
        primary_config_key="default_chat_model",
        role_label="默认",
        temperature=temperature,
        max_tokens=max_tokens,
        streaming=streaming,
        use_cache=use_cache,
        enable_fallback=enable_fallback,
        model_name=model_name,
        model_provider=model_provider,
        **kwargs,
    )


def get_helper_model(
    temperature: float | None = None,
    max_tokens: int | None = None,
    streaming: bool = False,
    use_cache: bool = True,
    **kwargs: Any,
) -> BaseChatModel:
    """返回辅助模型 + 降级链

    降级链：[辅助模型, 降级模型] — 辅助模型重试3次失败后切换到降级模型。
    降级模型来自 SystemConfig 的 fallback_chat_model 配置。

    Args:
        temperature: 温度参数
        max_tokens: 最大 token 数
        streaming: 是否流式（默认 False）
        use_cache: 是否使用缓存
        **kwargs: 其他参数

    Returns:
        ResilientModel 实例

    Raises:
        RuntimeError: 所有模型都不可用时
    """
    return _build_resilient_chain(
        primary_config_key="helper_model",
        role_label="辅助",
        temperature=temperature,
        max_tokens=max_tokens,
        streaming=streaming,
        use_cache=use_cache,
        enable_fallback=True,
        **kwargs,
    )


def get_structured_model(
    response_format: Any,
    streaming: bool = False,
    **kwargs: Any,
) -> BaseChatModel:
    """返回结构化输出模型 + 降级链

    逻辑：
    1. 获取默认模型 + 降级链（调用 get_default_model）
    2. 对降级链中的每个模型应用 with_structured_output(response_format)
    3. 返回新的 ResilientModel 实例

    注意：with_structured_output 返回的是 Runnable，不是 BaseChatModel。
    本函数采用方案 A：返回 ResilientModel，内部模型列表是
    with_structured_output 后的 Runnable 列表（ResilientModel.models
    字段类型为 List[Any]，可兼容 Runnable）。

    使用约束：
    - 返回的 ResilientModel 仅支持 invoke / ainvoke 接口
      （with_structured_output 的 Runnable 不支持 _generate / _stream）

    Args:
        response_format: Pydantic BaseModel 类或 JSON Schema
        streaming: 是否流式（默认 False，结构化输出推荐非流式）
        **kwargs: 其他参数（透传给 get_default_model）

    Returns:
        ResilientModel 实例（内部模型为 Runnable 列表）

    Raises:
        RuntimeError: 所有模型应用 with_structured_output 均失败
    """
    # 1. 获取默认模型 + 降级链
    base_model = get_default_model(streaming=streaming, **kwargs)

    # 2. 非 ResilientModel 时直接应用结构化输出
    if not isinstance(base_model, ResilientModel):
        return _apply_structured_output(base_model, response_format)

    # 3. 对降级链中的每个模型应用结构化输出
    structured_runnables: list[Any] = []
    for model in base_model.models:
        try:
            structured = _apply_structured_output(model, response_format)
            structured_runnables.append(structured)
        except Exception as e:
            logger.warning(
                f"模型应用结构化输出失败: {e}"
            )

    if not structured_runnables:
        raise RuntimeError(
            "所有模型应用结构化输出均失败"
        )

    # 4. 返回新的 ResilientModel 实例
    logger.info(
        f"已配置结构化输出模型 + 降级链: {len(structured_runnables)} 个模型"
    )
    return ResilientModel(models=structured_runnables)


def _apply_structured_output(model: Any, schema: Any) -> Any:
    """对单个模型应用结构化输出

    DeepSeek 模型使用 JSON mode（避免 thinking + tool_choice 冲突），
    其他模型使用 with_structured_output。

    Args:
        model: BaseChatModel 实例（含 _provider_id 属性）
        schema: Pydantic BaseModel 类

    Returns:
        结构化输出 Runnable（with_structured_output 返回值或 JsonModeStructuredModel）
    """
    provider_id = getattr(model, "_provider_id", None)
    if provider_id == "deepseek":
        # DeepSeek thinking mode 与 tool_choice 冲突，使用 JSON mode
        from .llm_factory import JsonModeStructuredModel
        model_name = getattr(model, "model", "") or getattr(model, "model_name", "")
        json_model = model.bind(response_format={"type": "json_object"})
        return JsonModeStructuredModel(json_model, schema, provider_id, model_name)
    return model.with_structured_output(schema)


def get_streaming_model(
    model_name: str | None = None,
    model_provider: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    use_cache: bool = True,
    **kwargs: Any,
) -> BaseChatModel:
    """返回流式模型 + 降级链

    便捷方法，等价于 get_default_model(streaming=True, ...)

    Args:
        model_name: 模型名称
        model_provider: 模型提供商
        temperature: 温度参数
        max_tokens: 最大 token 数
        use_cache: 是否使用缓存
        **kwargs: 其他参数（透传给 get_default_model）

    Returns:
        ResilientModel 实例

    Raises:
        RuntimeError: 所有模型都不可用时
    """
    return get_default_model(
        model_name=model_name,
        model_provider=model_provider,
        temperature=temperature,
        max_tokens=max_tokens,
        streaming=True,
        use_cache=use_cache,
        **kwargs,
    )
