from __future__ import annotations

import logging

from Django_xm.apps.agent_hub.config import resolve_agent_type_str

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
        中间件实例列表。安全类注入失败（ApprovalMiddleware / guardrails 能力）
        向上抛异常使 agent 创建失败（round3 2.3 fail-closed）；非安全类
        （ContextManager / ToolExecutionTimeout）失败降级返回不含该中间件的栈。

    Raises:
        RuntimeError: ApprovalMiddleware 注入失败 / guardrails 能力或
            SAFE_RAG guardrails 栈构建失败（安全边界缺口，fail-closed）。
    """
    middleware_stack = []

    if config.middleware is not None and len(config.middleware) > 0:
        middleware_stack.extend(config.middleware)
        logger.info(f"使用显式中间件 ({len(config.middleware)} 个)")

    try:
        from Django_xm.apps.ai_engine.capabilities import registry
    except Exception as e:
        # fail-closed（round3 2.3）：能力注册表模块加载失败意味着安全能力
        # （guardrails）与其他治理能力全部不可用，静默降级会让所有 agent
        # 带缺口运行——向上抛出使 agent 创建失败。
        raise RuntimeError(f"CapabilityRegistry 模块加载失败（fail-closed）: {e}") from e

    capabilities = config.capabilities
    if capabilities is None:
        capabilities = registry.get_default_capabilities(resolve_agent_type_str(config))

    # 逐能力安全分类在 registry.build_middleware_for_agent 内实施（round3 2.3）：
    # - 安全类（guardrails）构建失败向上抛（fail-closed）；
    # - 非安全类（context_management/tool_injection/rate_limit）降级跳过。
    # 故此处不再整体吞异常——仅安全类失败会到达此处并使 agent 创建失败。
    auto_middleware = registry.build_middleware_for_agent(
        resolve_agent_type_str(config),
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

    if config.agent_type.value == "safe_rag":
        # safe_rag 专属 guardrails 栈：safe_rag 的核心承诺即强制护栏
        # （AgentConfig.validate 强制 enable_guardrails=True），构建失败
        # 静默降级会让 safe_rag 名不副实——fail-closed 抛出（round3 2.3）。
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
            raise RuntimeError(f"SAFE_RAG guardrails 栈构建失败（fail-closed）: {e}") from e

    # ── 收敛点保底注入（spec: unify-context-injection）──
    # 在役路径（BASE/DEEP_RESEARCH）栈中已含同类型实例时幂等跳过（零行为变化）；
    # 死路径（SubAgentBuilder 等）与未来新增构建路径自动获得基础能力。
    # 追加位置在栈末，与 base/deep 现有手动 ensure 的追加位置等效。
    #
    # 安全分类（round3 2.3，逐中间件核定）：
    # - ApprovalMiddleware：审批是工具执行的安全边界（HIGH/CONTROLLED 工具
    #   必须人工确认）→ 注入失败 fail-closed 抛出（与 approval/middleware
    #   内部 fail-closed 语义对齐），不得降级为无审批 agent；
    # - ContextManagerMiddleware / ToolExecutionTimeoutMiddleware：非安全类
    #   （上下文治理 / 资源保护），注入失败降级 warning，agent 仍可运行。
    # 安全类：fail-closed
    from Django_xm.apps.agent_hub.builders.middleware_utils import ensure_approval_middleware

    ensure_approval_middleware(middleware_stack)
    # 非安全类：维持降级
    try:
        ensure_context_middleware(middleware_stack, config)
    except Exception as e:
        logger.warning(f"ContextManagerMiddleware 收敛点保底注入失败(非致命): {e}")

    try:
        ensure_tool_timeout_middleware(middleware_stack)
    except Exception as e:
        logger.warning(f"ToolExecutionTimeoutMiddleware 收敛点保底注入失败(非致命): {e}")

    logger.info(f"最终中间件栈: {len(middleware_stack)} 个")
    return middleware_stack


def ensure_tool_timeout_middleware(middleware_stack: list) -> bool:
    """确保 ToolExecutionTimeoutMiddleware 已注入中间件栈（B6/P2-13 不对齐项②）。

    通用工具级缺省执行超时（默认 300s，AGENT_TOOL_EXECUTION_TIMEOUT 可覆盖），
    约束无自带超时的工具，避免单个挂死工具无限推迟全局 hard_timeout 判定
    （硬超时仅在事件间隙检查）。经收敛点注入使 base/deep/subagent 三 builder
    构建的全部 agent 自动获得；栈中已存在时幂等跳过。

    Args:
        middleware_stack: 中间件栈（原地修改）。

    Returns:
        True 表示本次新注入；False 表示栈中已存在或注入失败（降级不抛异常）。
    """
    try:
        from Django_xm.apps.ai_engine.services.tool_timeout import ToolExecutionTimeoutMiddleware

        if any(isinstance(m, ToolExecutionTimeoutMiddleware) for m in middleware_stack):
            return False
        middleware_stack.append(ToolExecutionTimeoutMiddleware())
        logger.info("已注入 ToolExecutionTimeoutMiddleware 到 agent 中间件栈（收敛点保底）")
        return True
    except Exception as e:
        logger.warning(f"ToolExecutionTimeoutMiddleware 保底注入失败(非致命): {e}")
        return False


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
                thread_id=config.session_id,
            )
        )
        logger.info("已注入 ContextManagerMiddleware 到 agent 中间件栈（收敛点保底）")
        return True
    except Exception as e:
        logger.warning(f"ContextManagerMiddleware 保底注入失败(非致命): {e}")
        return False
