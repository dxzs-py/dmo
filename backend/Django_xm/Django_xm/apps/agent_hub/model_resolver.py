from __future__ import annotations

import logging

from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)


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

        # 深度思考统一注入：当 enable_deep_thinking=True 且 special_params 中无 thinking 配置时，
        # 从 provider registry 读取默认 enabled_value 并合并
        if getattr(config, "enable_deep_thinking", False) and "thinking" not in special_params:
            try:
                from Django_xm.apps.ai_engine.services.registry_service import get_provider_config
                provider_cfg = get_provider_config(model_provider)
                thinking_cfg = provider_cfg.get("special_params", {}).get("thinking")
                if thinking_cfg and thinking_cfg.get("enabled_value"):
                    special_params["thinking"] = thinking_cfg["enabled_value"]
                    logger.info("已从 provider 配置自动注入 deep_thinking 参数")
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
