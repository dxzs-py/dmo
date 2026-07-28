"""审批策略定义

每个策略封装一个工具的审批条件判断逻辑。
策略只负责"是否需要审批"和"审批元数据构建"，
不负责实际执行拦截（拦截由 ApprovalMiddleware 统一处理）。

风险分级（RiskLevel）：
    - SAFE: 自动通过，不 interrupt，仅审计（auto_approved=True）
    - CONTROLLED: 需用户审批，常规 UI
    - HIGH: 需用户审批，红名高亮 + 强制 Docker 沙箱执行

子 agent 风险加权（assess_risk 的 subagent_context 参数）：
    - 纯只读工具（搜索、读文件相对路径）：不加权
    - 写操作/命令执行类工具：在子 agent 中上调一级（CONTROLLED → HIGH）
    - 但不超过 subagent_risk_ceiling（角色风险上限）
"""

import os

from Django_xm.common.risk_levels import RiskLevel, from_danger_level


class ApprovalPolicy:
    """审批策略基类

    子类需实现：
        - should_approve: 判断是否需要审批（默认基于 assess_risk != SAFE）
        - build_operation_desc: 构建操作描述（前端展示）

    可选重写：
        - assess_danger: 评估旧格式危险等级（默认 medium）
        - assess_risk: 评估新 RiskLevel 等级（默认从 assess_danger 转换）
        - build_title: 构建审批标题
        - build_description: 构建审批描述
    """

    tool_name: str = ""
    # 是否写操作/命令执行类（子 agent 中上调一级），只读工具设为 False
    is_write_operation: bool = False

    def should_approve(self, args: dict) -> bool:
        """返回 True 表示需要审批，False 表示自动通过。

        默认实现：基于 assess_risk 判断，SAFE 级自动通过，其他需审批。
        子类可重写以实现更复杂逻辑（如黑白名单）。
        """
        return self.assess_risk(args) != RiskLevel.SAFE

    def assess_danger(self, args: dict) -> str:
        """评估旧格式危险等级：low/medium/high。

        保留以兼容 DB 字段 danger_level。
        新代码应使用 assess_risk()。
        """
        return "medium"

    def assess_risk(self, args: dict, *, subagent_context: dict | None = None) -> RiskLevel:
        """评估风险等级：SAFE/CONTROLLED/HIGH。

        默认实现：从 assess_danger 转换。
        子类应重写以实现精确的风险评估。

        Args:
            args: 工具调用参数
            subagent_context: 子 agent 上下文（None=主 agent）：
                - risk_ceiling: RiskLevel，子 agent 角色风险上限
                - depth: int，嵌套层级（0=主 agent）
                - agent_name: str，子 agent 名称
        """
        return self._apply_subagent_weighting(
            from_danger_level(self.assess_danger(args)),
            subagent_context,
        )

    def _apply_subagent_weighting(
        self,
        risk: RiskLevel,
        subagent_context: dict | None,
    ) -> RiskLevel:
        """子 agent 风险加权。

        - 纯只读工具（is_write_operation=False）：不加权
        - 写操作/命令执行类（is_write_operation=True）：在子 agent 中上调一级
          （CONTROLLED → HIGH；SAFE 保持 SAFE）
        - 不超过 risk_ceiling（角色风险上限）

        Args:
            risk: 原始风险等级
            subagent_context: 子 agent 上下文（None=主 agent，不加权）

        Returns:
            加权后的风险等级
        """
        if subagent_context is None:
            return risk  # 主 agent，不加权

        # 写操作/命令执行类：子 agent 中上调一级
        if self.is_write_operation and risk == RiskLevel.CONTROLLED:
            risk = RiskLevel.HIGH

        # 应用角色风险上限
        risk_ceiling = subagent_context.get('risk_ceiling')
        if risk_ceiling is not None:
            if risk_ceiling == RiskLevel.SAFE:
                return RiskLevel.SAFE
            if risk_ceiling == RiskLevel.CONTROLLED and risk == RiskLevel.HIGH:
                return RiskLevel.CONTROLLED

        return risk

    def build_operation_desc(self, args: dict) -> str:
        """构建 operation 描述（前端展示）"""
        raise NotImplementedError

    def build_title(self, args: dict) -> str:
        """构建审批标题"""
        return f"确认执行 {self.tool_name}"

    def build_description(self, args: dict) -> str:
        """构建审批描述"""
        return f"Agent 请求执行 {self.tool_name}"


class ShellExecApprovalPolicy(ApprovalPolicy):
    """shell_exec 工具审批策略

    风险分级（RiskLevel）：
        - 黑名单命令 → SAFE（工具内部直接拒绝，不需审批）
        - 白名单命令 → SAFE（安全命令，自动通过）
        - HIGH_RISK_KEYWORDS 命中 → HIGH（install/remove/delete/format/write）
        - 其他命令 → CONTROLLED
    """

    tool_name = "shell_exec"
    is_write_operation = True  # 命令执行类，子 agent 中上调一级

    # 触发 high 危险等级的关键字
    _HIGH_RISK_KEYWORDS = ("install", "remove", "delete", "format", "write")

    def assess_risk(self, args: dict, *, subagent_context: dict | None = None) -> RiskLevel:
        """评估 shell_exec 风险等级。"""
        command = args.get("command", "")
        if not command:
            return RiskLevel.SAFE  # 空命令，不执行

        # 延迟导入避免循环依赖
        from Django_xm.apps.tools.langchain.shell import (
            _is_command_blocked,
            _is_command_whitelisted,
        )

        # 黑名单命令：工具内部直接拒绝，不需审批
        if _is_command_blocked(command):
            return RiskLevel.SAFE

        # 白名单命令：安全命令，自动通过
        if _is_command_whitelisted(command):
            return RiskLevel.SAFE

        # 检查 HIGH_RISK_KEYWORDS
        command_lower = command.lower()
        for keyword in self._HIGH_RISK_KEYWORDS:
            if keyword in command_lower:
                return self._apply_subagent_weighting(RiskLevel.HIGH, subagent_context)

        return self._apply_subagent_weighting(RiskLevel.CONTROLLED, subagent_context)

    def assess_danger(self, args: dict) -> str:
        """旧格式危险等级（兼容 DB 字段 danger_level）。"""
        risk = self.assess_risk(args)
        if risk == RiskLevel.HIGH:
            return "high"
        elif risk == RiskLevel.SAFE:
            return "low"
        return "medium"

    def build_operation_desc(self, args: dict) -> str:
        return args.get("command", "")


class FileReaderApprovalPolicy(ApprovalPolicy):
    """file_reader 工具审批策略

    绝对路径读取需要审批，相对路径自动通过。
    """

    tool_name = "file_reader"

    def should_approve(self, args: dict) -> bool:
        file_path = args.get("file_path", "")
        return os.path.isabs(file_path)

    def build_operation_desc(self, args: dict) -> str:
        return args.get("file_path", "")


class FsWriteFileApprovalPolicy(ApprovalPolicy):
    """fs_write_file 工具审批策略

    写文件操作风险较高：
    - 绝对路径写入需要审批
    - 危险等级固定为 high
    """

    tool_name = "fs_write_file"

    def should_approve(self, args: dict) -> bool:
        relative_path = args.get("relative_path", "")
        return os.path.isabs(relative_path)

    def assess_danger(self, args: dict) -> str:
        return "high"

    def build_operation_desc(self, args: dict) -> str:
        return args.get("relative_path", "")


class AgentCleanupApprovalPolicy(ApprovalPolicy):
    """agent_cleanup 工具审批策略

    批量清理（未指定 agent_id）需要审批，
    单个清理自动通过。
    """

    tool_name = "agent_cleanup"

    def should_approve(self, args: dict) -> bool:
        return args.get("agent_id", "") == ""

    def build_operation_desc(self, args: dict) -> str:
        return "批量清理所有 Agent"


# ── deepagents 框架工具审批策略 ──────────────────────────────────
# deepagents FilesystemMiddleware 提供的工具名称与项目自定义工具不同：
#   deepagents: write_file / edit_file / read_file / execute
#   项目自定义: fs_write_file / edit_file / file_reader / shell_exec
# ApprovalMiddleware 用 tool_name 作为 key 查找策略，
# 因此需要为 deepagents 工具名称注册独立策略。
# 策略逻辑与项目自定义工具一致，仅 tool_name 和参数字段名不同。


class ExecuteApprovalPolicy(ShellExecApprovalPolicy):
    """deepagents execute 工具审批策略

    继承 ShellExecApprovalPolicy 的黑白名单逻辑，
    仅覆盖 tool_name 以匹配 deepagents 的 execute 工具。
    """

    tool_name = "execute"


class WriteFileApprovalPolicy(ApprovalPolicy):
    """deepagents write_file 工具审批策略

    deepagents 的 write_file 使用 file_path 参数（非 relative_path）。
    写文件操作风险较高，固定为 high。
    """

    tool_name = "write_file"

    def should_approve(self, args: dict) -> bool:
        # deepagents write_file 总是需要审批（写副作用）
        return True

    def assess_danger(self, args: dict) -> str:
        return "high"

    def build_operation_desc(self, args: dict) -> str:
        return args.get("file_path", "")

    def build_title(self, args: dict) -> str:
        return f"写入文件: {args.get('file_path', '')}"

    def build_description(self, args: dict) -> str:
        return f"Agent 请求写入文件 {args.get('file_path', '')}"


class EditFileApprovalPolicy(ApprovalPolicy):
    """deepagents edit_file 工具审批策略

    edit_file 修改文件内容，有写副作用，需要审批。
    deepagents 的 edit_file 使用 file_path 参数。
    """

    tool_name = "edit_file"

    def should_approve(self, args: dict) -> bool:
        # edit_file 总是需要审批（写副作用）
        return True

    def assess_danger(self, args: dict) -> str:
        return "high"

    def build_operation_desc(self, args: dict) -> str:
        path = args.get("file_path", "")
        return f"编辑文件: {path}"

    def build_title(self, args: dict) -> str:
        return f"编辑文件: {args.get('file_path', '')}"

    def build_description(self, args: dict) -> str:
        path = args.get("file_path", "")
        old_str = str(args.get("old_string", ""))[:80]
        return f"Agent 请求编辑文件 {path}，替换内容: {old_str}..."


class ReadFileApprovalPolicy(FileReaderApprovalPolicy):
    """deepagents read_file 工具审批策略

    继承 FileReaderApprovalPolicy 的绝对路径审批逻辑，
    仅覆盖 tool_name 以匹配 deepagents 的 read_file 工具。
    deepagents read_file 使用 file_path 参数。
    """

    tool_name = "read_file"

    def should_approve(self, args: dict) -> bool:
        file_path = args.get("file_path", "")
        return os.path.isabs(file_path)

    def build_operation_desc(self, args: dict) -> str:
        return args.get("file_path", "")
