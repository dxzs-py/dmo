"""审批策略定义

每个策略封装一个工具的审批条件判断逻辑。
策略只负责"是否需要审批"和"审批元数据构建"，
不负责实际执行拦截（拦截由 ApprovalMiddleware 统一处理）。
"""

import os


class ApprovalPolicy:
    """审批策略基类

    子类需实现：
        - should_approve: 判断是否需要审批
        - build_operation_desc: 构建操作描述（前端展示）

    可选重写：
        - assess_danger: 评估危险等级（默认 medium）
        - build_title: 构建审批标题
        - build_description: 构建审批描述
    """

    tool_name: str = ""

    def should_approve(self, args: dict) -> bool:
        """返回 True 表示需要审批，False 表示自动通过"""
        raise NotImplementedError

    def assess_danger(self, args: dict) -> str:
        """评估危险等级：low/medium/high"""
        return "medium"

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

    - 黑名单命令：不需审批（由工具内部直接拒绝）
    - 白名单命令：不需审批
    - 其他命令：需要审批
    """

    tool_name = "shell_exec"

    # 触发 high 危险等级的关键字
    _HIGH_RISK_KEYWORDS = ("install", "remove", "delete", "format", "write")

    def should_approve(self, args: dict) -> bool:
        command = args.get("command", "")
        if not command:
            return False

        # 延迟导入避免循环依赖
        from Django_xm.apps.tools.langchain.shell import (
            _is_command_blocked,
            _is_command_whitelisted,
        )

        # 黑名单命令：工具内部会直接拒绝，无需审批
        if _is_command_blocked(command):
            return False

        # 白名单命令：自动通过
        if _is_command_whitelisted(command):
            return False

        # 其他命令：需要审批
        return True

    def assess_danger(self, args: dict) -> str:
        command = args.get("command", "").lower()
        for keyword in self._HIGH_RISK_KEYWORDS:
            if keyword in command:
                return "high"
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
