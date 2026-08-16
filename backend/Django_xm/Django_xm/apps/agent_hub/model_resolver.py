from __future__ import annotations

import logging

from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)

# 已告警"thinking 配置缺少 disabled_value"的 provider 集合（模块级去重，避免刷屏）
_MISSING_DISABLED_VALUE_WARNED: set[str] = set()


def resolve_model(config) -> str | BaseChatModel:
    """解析模型配置，返回带 fallback 的模型实例

    所有模型创建路径都通过 get_chat_model()，自动获得 fallback 能力。
    仅当 config.model 是 BaseChatModel 实例时直接返回（用户显式指定）。
    """
    if isinstance(config.model, BaseChatModel):
        logger.info(f"使用自定义模型实例: {config.model.__class__.__name__}")
        return config.model

    # 统一通过 get_chat_model 创建（默认启用 fallback）
    try:
        from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model

        model_provider = config.provider_id or None
        model_name = config.model_name or None

        # 如果 config.model 是字符串（如 "openai:gpt-4o"），解析出 provider 和 name
        if isinstance(config.model, str) and ":" in config.model:
            model_provider, model_name = config.model.split(":", 1)

        special_params = getattr(config, "special_params", None) or {}

        # 深度思考统一注入（enabled / disabled 双向）：
        # - enable_deep_thinking=True 且 special_params 无 thinking：注入 enabled_value；
        # - enable_deep_thinking=False 且 special_params 无 thinking：注入 disabled_value
        #   （显式禁用，防止 provider 默认开启深度思考）；provider 未配置 disabled_value
        #   时告警一次（模块级 set 去重，避免每次创建 agent 刷屏）。
        if "thinking" not in special_params:
            try:
                from Django_xm.apps.ai_engine.services.registry_service import get_provider_config
                provider_cfg = get_provider_config(model_provider)
                thinking_cfg = (provider_cfg.get("special_params") or {}).get("thinking") or {}
                if getattr(config, "enable_deep_thinking", False):
                    if thinking_cfg.get("enabled_value"):
                        special_params["thinking"] = thinking_cfg["enabled_value"]
                        logger.info("已从 provider 配置自动注入 deep_thinking 启用参数")
                elif thinking_cfg:
                    if thinking_cfg.get("disabled_value"):
                        special_params["thinking"] = thinking_cfg["disabled_value"]
                        logger.info("已从 provider 配置自动注入 deep_thinking 禁用参数")
                    elif model_provider not in _MISSING_DISABLED_VALUE_WARNED:
                        _MISSING_DISABLED_VALUE_WARNED.add(model_provider)
                        logger.warning(
                            f"provider={model_provider} 的 thinking 配置缺少 disabled_value，"
                            "无法显式注入禁用参数（该模型可能默认开启深度思考）"
                        )
            except Exception as e:
                logger.warning(f"无法自动注入 deep_thinking 参数: {e}")

        model = get_chat_model(
            model_name=model_name,
            model_provider=model_provider,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            special_params=special_params or None,
            enable_fallback=True,
        )
        if model is not None:
            logger.info(f"使用模型 (provider={model_provider}, name={model_name}, 带 fallback)")
            return model
    except Exception as e:
        logger.warning(f"get_chat_model 创建失败: {e}")

    # 最终降级：返回模型字符串，让 create_agent 内部处理
    if isinstance(config.model, str):
        logger.info(f"降级使用模型标识符: {config.model}")
        return config.model

    from Django_xm.apps.ai_engine.services.llm_factory import get_model_string

    model_str = get_model_string()
    logger.info(f"降级使用默认模型字符串: {model_str}")
    return model_str
