from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

def build_middleware(config) -> list:
    middleware_stack = []

    if config.middleware is not None and len(config.middleware) > 0:
        middleware_stack.extend(config.middleware)
        logger.info(f"使用显式中间件 ({len(config.middleware)} 个)")

    try:
        from Django_xm.apps.ai_engine.capabilities import registry
        capabilities = config.capabilities
        if capabilities is None:
            capabilities = registry.get_default_capabilities(_get_agent_type_str(config))

        auto_middleware = registry.build_middleware_for_agent(
            _get_agent_type_str(config),
            capabilities,
            user_id=config.user_id,
            model_name=config.model_name,
            store=config.store,
            enable_guardrails=config.enable_guardrails,
            guardrails_strict_mode=config.guardrails_strict_mode,
            enable_pii=config.enable_pii,
            enable_human_in_loop=config.enable_human_in_loop,
            thread_id=config.session_id,
        )
        if auto_middleware:
            existing_types = {type(m) for m in middleware_stack}
            for m in auto_middleware:
                if type(m) not in existing_types:
                    middleware_stack.append(m)
                    existing_types.add(type(m))
            logger.info(f"通过 CapabilityRegistry 追加 {len(auto_middleware)} 个中间件")
    except Exception as e:
        logger.warning(f"CapabilityRegistry 中间件构建失败: {e}")

    if config.agent_type.value == "safe_rag":
        try:
            from Django_xm.apps.ai_engine.guardrails.middleware import create_standard_guardrails
            guardrails = create_standard_guardrails(
                enable_input_validation=config.enable_input_validation,
                enable_output_validation=config.enable_output_validation,
                strict_mode=config.guardrails_strict_mode,
                enable_pii=config.enable_pii,
                enable_human_in_loop=config.enable_human_in_loop,
            )
            existing_types = {type(m) for m in middleware_stack}
            for m in guardrails:
                if type(m) not in existing_types:
                    middleware_stack.append(m)
            logger.info("SAFE_RAG 模式已注入 guardrails 栈")
        except Exception as e:
            logger.warning(f"Guardrails 栈构建失败: {e}")

    logger.info(f"最终中间件栈: {len(middleware_stack)} 个")
    return middleware_stack

def _get_agent_type_str(config) -> str:
    type_map = {
        "base": "base",
        "rag": "base",
        "safe_rag": "base",
        "deep_research": "deep_research",
        "deep_research_custom": "deep_research",
        "web_researcher": "base",
        "doc_analyst": "base",
        "report_writer": "base",
    }
    return type_map.get(config.agent_type.value, "base")
