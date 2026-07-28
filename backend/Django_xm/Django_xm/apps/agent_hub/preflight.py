"""Agent 执行预检

在 AgentFactory.create() 中调用，提前检查关键依赖是否可用。
预检失败不阻止创建，而是记录问题并标记降级。
"""

import logging

from Django_xm.apps.agent_hub.config import AgentType

logger = logging.getLogger(__name__)


class PreflightResult:
    """预检结果"""

    def __init__(self, passed: bool, issues: list[str], warnings: list[str]):
        self.passed = passed
        self.issues = issues        # 严重问题（阻止正常执行）
        self.warnings = warnings    # 警告（可能影响功能）

    def __repr__(self):
        return (
            f"PreflightResult(passed={self.passed}, "
            f"issues={self.issues}, warnings={self.warnings})"
        )


class ExecutionPreflight:
    """Agent 创建前预检

    检查关键依赖是否可用，提前发现问题。
    预检失败不阻止 agent 创建，而是记录问题让运行时韧性机制处理。
    """

    async def check(self, config) -> PreflightResult:
        """执行预检

        Args:
            config: AgentConfig 实例

        Returns:
            PreflightResult 包含通过状态、问题和警告
        """
        issues = []
        warnings = []

        # 1. LLM 可达性检查（轻量级：仅验证模型创建成功）
        llm_ok, llm_msg = await self._check_llm_reachable(config)
        if not llm_ok:
            issues.append(f"LLM 服务不可用: {llm_msg}")

        # 2. Redis 连接检查（仅深度研究类型需要）
        if config.agent_type in (AgentType.DEEP_RESEARCH, AgentType.DEEP_RESEARCH_CUSTOM):
            redis_ok, redis_msg = await self._check_redis_connected()
            if not redis_ok:
                issues.append(f"Redis 连接失败: {redis_msg}")

        # 3. Checkpointer 可用性检查（深度研究需要）
        if config.agent_type in (AgentType.DEEP_RESEARCH, AgentType.DEEP_RESEARCH_CUSTOM):
            cp_ok, cp_msg = self._check_checkpointer(config)
            if not cp_ok:
                warnings.append(f"Checkpointer 不可用: {cp_msg}")

        # 4. 工具可用性检查（可选，仅警告）
        tool_warnings = self._check_tools(config)
        warnings.extend(tool_warnings)

        return PreflightResult(
            passed=len(issues) == 0,
            issues=issues,
            warnings=warnings,
        )

    async def _check_llm_reachable(self, config) -> tuple[bool, str]:
        """检查 LLM 服务可达性

        轻量级检查：尝试通过 model_resolver 创建模型实例。
        不发送实际请求，仅验证模型配置和连接参数有效。
        """
        try:
            from Django_xm.apps.agent_hub.model_resolver import resolve_model
            model = resolve_model(config)
            if model is not None:
                return (True, "")
            return (False, "模型解析返回 None")
        except Exception as e:
            return (False, str(e)[:200])

    async def _check_redis_connected(self) -> tuple[bool, str]:
        """检查 Redis 连接"""
        try:
            from django.core.cache import cache
            cache.set("_preflight_check", "1", timeout=5)
            result = cache.get("_preflight_check")
            if result == "1":
                return (True, "")
            return (False, "Redis 读写不一致")
        except Exception as e:
            return (False, str(e)[:200])

    def _check_checkpointer(self, config) -> tuple[bool, str]:
        """检查 Checkpointer 可用性"""
        if config.checkpointer is not None:
            return (True, "")
        # 未配置 checkpointer，深度研究需要但可降级
        return (False, "未配置 checkpointer")

    def _check_tools(self, config) -> list[str]:
        """检查工具可用性（仅警告）"""
        warnings = []
        if config.tools is not None and len(config.tools) == 0:
            warnings.append("工具列表为空，agent 将无法使用工具")
        return warnings
