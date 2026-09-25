from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def build_middleware(config) -> list:
    """构建 agent 中间件栈（base/deep/subagent 三 builder 的必经收敛点）。

    组装顺序：①config.middleware 显式栈 → ②CapabilityRegistry 能力产出
    （capabilities 为 None 时取 ai_engine 默认表）→ ③safe_rag 专属 guardrails
    → ④收敛点保底注入（ApprovalMiddleware + ContextManagerMiddleware）。

    设计依据（收敛点保底注入，spec: unify-context-injection）：
    参考 Claude Code 权限执行层统一模式（能力挂载与 agent 类型无关）与
    langgraph 官方模式（middleware 在组装点统一挂载）——任何现在/未来的
    agent 构建路径自动获得审批 + 上下文管理基础能力，不再依赖每个 builder
    记得接线。在役路径（BASE/DEEP_RESEARCH）栈中已含同类型实例时幂等跳过，
    中间件栈内容与顺序零变化；保底仅在栈中缺失时于栈末追加（与 base/deep
    现有手动 ensure 的追加位置等效）。修复质量报告 lc-02（死路径防御）。

    Args:
        config: AgentConfig（读取 middleware/capabilities/agent_type 等）。

    Returns:
        中间件实例列表（保底注入失败时降级返回不含该中间件的栈，不抛异常）。
    """
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
            from Django_xm.apps.ai_engine.guardrails import create_standard_guardrails

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

    # ── 收敛点保底注入（spec: unify-context-injection）──
    # 在役路径（BASE/DEEP_RESEARCH）栈中已含同类型实例时幂等跳过（零行为变化）；
    # 死路径（SubAgentBuilder 等）与未来新增构建路径自动获得基础能力。
    # 追加位置在栈末，与 base/deep 现有手动 ensure 的追加位置等效。
    try:
        from Django_xm.apps.agent_hub.builders.middleware_utils import ensure_approval_middleware

        ensure_approval_middleware(middleware_stack)
    except Exception as e:
        logger.warning(f"ApprovalMiddleware 收敛点保底注入失败(非致命): {e}")

    try:
        ensure_context_middleware(middleware_stack, config)
    except Exception as e:
        logger.warning(f"ContextManagerMiddleware 收敛点保底注入失败(非致命): {e}")

    logger.info(f"最终中间件栈: {len(middleware_stack)} 个")
    return middleware_stack


def ensure_context_middleware(middleware_stack: list, config) -> bool:
    """确保 ContextManagerMiddleware 已注入中间件栈（缺失则追加，收敛点保底）。

    设计依据（spec: unify-context-injection）：参考 Claude Code 权限执行层
    统一模式（能力挂载与 agent 类型无关）与 langgraph 官方模式（middleware
    在组装点统一挂载）——任何经 build_middleware 构建的 agent 自动获得
    上下文管理能力，不再依赖每个 builder 记得接线。在役路径
    （BASE/DEEP_RESEARCH）栈中已含该中间件时幂等跳过，零行为变化；
    死路径经收敛点保底覆盖（质量报告 lc-02 的防御性修复）。

    Args:
        middleware_stack: 中间件栈（原地修改）。
        config: AgentConfig，提供 model_name / user_id / store / session_id。

    Returns:
        True 表示本次新注入；False 表示栈中已存在或注入失败（降级不抛异常）。
    """
    try:
        from Django_xm.apps.context_manager.middleware import ContextManagerMiddleware

        if any(isinstance(m, ContextManagerMiddleware) for m in middleware_stack):
            return False
        middleware_stack.append(
            ContextManagerMiddleware(
                model_name=config.model_name,
                user_id=config.user_id,
                store=config.store,
                thread_id=config.session_id,
            )
        )
        logger.info("已注入 ContextManagerMiddleware 到 agent 中间件栈（收敛点保底）")
        return True
    except Exception as e:
        logger.warning(f"ContextManagerMiddleware 保底注入失败(非致命): {e}")
        return False


def _get_agent_type_str(config) -> str:
    type_map = {
        "base": "base",
        "rag": "base",
        "safe_rag": "base",
        "deep_research": "deep_research",
    }
    return type_map.get(config.agent_type.value, "base")
