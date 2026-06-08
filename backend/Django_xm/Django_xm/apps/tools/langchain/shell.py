"""Shell 命令执行工具

提供安全的 Shell 命令执行能力，支持：
- 命令白名单：白名单内命令直接执行
- 危险命令拦截：禁止执行 rm -rf、format、shutdown 等
- 用户确认机制：非白名单命令通过 LangGraph interrupt 暂停，等待用户确认后 resume
- 超时控制：防止命令挂起
- 输出截断：防止返回内容过大
- 工作目录限制：限制命令执行范围
"""

import os
import re
import subprocess
import platform
import logging
import asyncio
from typing import Optional, List
from pathlib import Path

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from Django_xm.apps.tools.errors import StandardToolResult, ToolStatus, TOOL_VERSION
from Django_xm.apps.tools.base import AsyncToolMixin, interrupt_for_approval

logger = logging.getLogger(__name__)


# ── 安全配置 ──────────────────────────────────────────────────────

# 白名单命令前缀（这些命令可直接执行，无需用户确认）
DEFAULT_WHITELIST_COMMANDS: List[str] = [
    # 浏览器自动化
    "agent-browser",
    # 文档转换
    "pandoc",
    # Python（只读/安全操作）
    "python",
    "python3",
    "pip list",
    "pip show",
    "pip check",
    "pip3 list",
    "pip3 show",
    "pip3 check",
    # Node
    "node",
    "npm list",
    "npm view",
    "npm info",
    "npx",
    # 系统信息（只读）
    "echo",
    "dir",
    "ls",
    "type",
    "cat",
    "head",
    "tail",
    "wc",
    "find",
    "where",
    "which",
    "whoami",
    "hostname",
    # Git（只读）
    "git status",
    "git log",
    "git diff",
    "git branch",
    "git show",
    "git remote",
    # 网络（只读）
    "curl",
    "ping",
]

# 严格禁止的命令模式（无论是否在白名单中都不允许）
BLOCKED_PATTERNS: List[str] = [
    # 删除
    r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+|.*--no-preserve-root)",
    r"\brmdir\s+/s",
    r"\bdel\s+/[sS]",
    r"\brd\s+/[sS]",
    r"\bformat\s+[a-zA-Z]:",
    # 系统操作
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bhalt\b",
    r"\bpoweroff\b",
    # 权限提升
    r"\bsudo\s+rm\b",
    r"\bsu\b",
    # 危险重定向
    r">\s*/dev/sd",
    r"\bdd\s+if=",
    # 网络危险操作
    r"\bmkfs\b",
    r"\bmount\b",
    r"\bumount\b",
    # 进程杀杀杀
    r"\bkill\s+-9\s+1\b",
    r"\btaskkill\s+/f\s+/pid\s+0\b",
    # 危险脚本下载执行
    r"\bcurl\s+.*\|\s*(bash|sh|python|pwsh)",
    r"\bwget\s+.*\|\s*(bash|sh|python|pwsh)",
]

# 最大输出长度（字符）
MAX_OUTPUT_LENGTH = 50000

# 默认超时（秒）
DEFAULT_TIMEOUT = 60

# 最大超时（秒）
MAX_TIMEOUT = 300


def _get_whitelist_commands() -> List[str]:
    """获取白名单命令列表（支持 Django settings 覆盖）"""
    try:
        from django.conf import settings as django_settings
        custom = getattr(django_settings, "SHELL_EXEC_WHITELIST", None)
        if custom is not None:
            return list(custom)
    except (ImportError, AttributeError):
        pass
    return DEFAULT_WHITELIST_COMMANDS


def _is_command_blocked(command: str) -> Optional[str]:
    """检查命令是否匹配禁止模式，返回匹配的模式描述或 None"""
    cmd_lower = command.lower().strip()
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, cmd_lower, re.IGNORECASE):
            return f"匹配禁止模式: {pattern}"
    return None


def _is_command_whitelisted(command: str) -> bool:
    """检查命令是否在白名单中"""
    whitelist = _get_whitelist_commands()
    cmd_stripped = command.strip()

    # 精确匹配
    for wl_cmd in whitelist:
        if cmd_stripped == wl_cmd:
            return True

    # 前缀匹配：命令以白名单命令开头，且紧跟空格或参数
    cmd_first_word = cmd_stripped.split()[0] if cmd_stripped.split() else ""
    for wl_cmd in whitelist:
        wl_first_word = wl_cmd.split()[0]
        # 白名单命令本身包含空格（如 "git status"），需要完整前缀匹配
        if " " in wl_cmd:
            if cmd_stripped.startswith(wl_cmd + " ") or cmd_stripped == wl_cmd:
                return True
        # 单词白名单命令，匹配第一个词
        elif cmd_first_word == wl_first_word:
            return True

    return False


def _get_safe_working_dir(working_dir: str = "") -> str:
    """获取安全的工作目录"""
    if working_dir:
        wd = Path(working_dir).resolve()
        # 检查是否在允许的目录范围内
        try:
            from django.conf import settings as django_settings
            allowed_dirs = getattr(django_settings, "SHELL_EXEC_ALLOWED_DIRS", None)
            if allowed_dirs:
                for ad in allowed_dirs:
                    if str(wd).startswith(str(Path(ad).resolve())):
                        return str(wd)
                # 不在允许目录中
                return os.getcwd()
        except (ImportError, AttributeError):
            pass
        return str(wd)
    return os.getcwd()


def _execute_command(
    command: str,
    timeout: int = DEFAULT_TIMEOUT,
    working_dir: str = "",
) -> StandardToolResult:
    """执行 Shell 命令并返回结果"""
    timeout = min(timeout, MAX_TIMEOUT)
    cwd = _get_safe_working_dir(working_dir)

    try:
        is_windows = platform.system() == "Windows"
        if is_windows:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                encoding="utf-8",
                errors="replace",
            )
        else:
            result = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                encoding="utf-8",
                errors="replace",
            )

        stdout = result.stdout or ""
        stderr = result.stderr or ""

        # 截断输出
        if len(stdout) > MAX_OUTPUT_LENGTH:
            stdout = stdout[:MAX_OUTPUT_LENGTH] + f"\n... [输出已截断，共 {len(result.stdout or '')} 字符]"
        if len(stderr) > MAX_OUTPUT_LENGTH // 4:
            stderr = stderr[:MAX_OUTPUT_LENGTH // 4] + f"\n... [错误输出已截断]"

        output_parts = []
        if stdout.strip():
            output_parts.append(stdout.strip())
        if stderr.strip():
            output_parts.append(f"[stderr]\n{stderr.strip()}")

        output = "\n\n".join(output_parts) if output_parts else "(命令执行完成，无输出)"

        if result.returncode != 0:
            return StandardToolResult(
                content=f"命令退出码: {result.returncode}\n{output}",
                status=ToolStatus.ERROR,
                source="shell_exec",
                metadata={"return_code": result.returncode, "command": command},
            )

        return StandardToolResult(
            content=output,
            status=ToolStatus.SUCCESS,
            source="shell_exec",
            metadata={"return_code": result.returncode, "command": command},
        )

    except subprocess.TimeoutExpired:
        return StandardToolResult(
            content=f"命令执行超时（{timeout}秒），已终止。命令: {command}",
            status=ToolStatus.ERROR,
            source="shell_exec",
            metadata={"timeout": timeout, "command": command},
        )
    except FileNotFoundError as e:
        return StandardToolResult(
            content=f"命令未找到: {e}",
            status=ToolStatus.ERROR,
            source="shell_exec",
            metadata={"command": command},
        )
    except Exception as e:
        return StandardToolResult(
            content=f"命令执行异常: {type(e).__name__}: {e}",
            status=ToolStatus.ERROR,
            source="shell_exec",
            metadata={"command": command},
        )


# ── 工具定义 ──────────────────────────────────────────────────────

class ShellExecInput(BaseModel):
    """Shell 命令执行工具输入"""
    command: str = Field(
        description="要执行的 Shell 命令"
    )
    timeout: int = Field(
        default=DEFAULT_TIMEOUT,
        description=f"命令执行超时时间（秒），最大 {MAX_TIMEOUT} 秒"
    )
    working_dir: str = Field(
        default="",
        description="命令执行的工作目录，为空则使用当前目录"
    )


class ShellExecTool(AsyncToolMixin, BaseTool):
    """Shell 命令执行工具

    安全策略：
    1. 白名单命令：直接执行，无需确认
    2. 非白名单命令：通过 LangGraph interrupt 暂停执行，等待用户确认后 resume 继续执行
    3. 禁止命令：始终拒绝执行
    4. 超时控制：防止命令挂起
    5. 输出截断：防止返回内容过大
    """
    name: str = "shell_exec"
    version: str = TOOL_VERSION
    metadata: dict = {"tier": "extended", "visibility": "selectable", "category": "system"}
    description: str = (
        "执行 Shell 命令并返回输出结果。"
        "适用场景：需要运行命令行工具（如 agent-browser、pandoc、python 脚本等）、查看系统信息、执行自动化任务。"
        "不适用：文件读写（应使用 fs_read_file/fs_write_file）、网络搜索（应使用 web_search）。"
        "参数：command-要执行的命令（必填），timeout-超时秒数（默认60），working_dir-工作目录（默认当前目录）。"
        "安全策略：白名单命令（agent-browser、pandoc、python、node、git 等）直接执行；"
        "非白名单命令会暂停等待用户确认，用户确认后自动继续执行；"
        "危险命令（rm -rf、shutdown 等）始终拒绝。"
    )
    args_schema: type[BaseModel] = ShellExecInput

    def _run(
        self,
        command: str,
        timeout: int = DEFAULT_TIMEOUT,
        working_dir: str = "",
    ) -> str:
        """同步执行入口（由 _arun 调用，不直接使用 interrupt）"""
        logger.info(f"shell_exec: 收到命令请求: {command[:200]}")

        # 1. 检查禁止命令
        blocked_reason = _is_command_blocked(command)
        if blocked_reason:
            logger.warning(f"shell_exec: 命令被禁止: {command[:100]} ({blocked_reason})")
            return StandardToolResult(
                content=f"命令已被安全策略拦截: {blocked_reason}\n命令: {command}\n此命令属于危险操作，不允许执行。",
                status=ToolStatus.ERROR,
                source="shell_exec",
                metadata={"command": command, "blocked": True, "reason": blocked_reason},
            ).to_tool_message()

        # 2. 白名单命令直接执行
        if _is_command_whitelisted(command):
            logger.info(f"shell_exec: 白名单命令，直接执行: {command[:100]}")
            result = _execute_command(command, timeout, working_dir)
            return result.to_tool_message()

        # 3. 非白名单命令：不应在 _run 中直接处理，由 _arun 处理
        # 如果走到这里，说明是同步调用，直接拒绝
        logger.warning(f"shell_exec: 非白名单命令在同步模式下无法请求审批，拒绝执行: {command[:100]}")
        return StandardToolResult(
            content=f"非白名单命令需要用户确认，但当前为同步执行模式，无法请求审批。命令: {command}",
            status=ToolStatus.ERROR,
            source="shell_exec",
            metadata={"command": command, "sync_mode": True},
        ).to_tool_message()

    async def _arun(
        self,
        command: str,
        timeout: int = DEFAULT_TIMEOUT,
        working_dir: str = "",
        run_manager=None,
        config=None,
    ) -> str:
        """异步执行入口：在异步上下文中调用 interrupt()，确保 LangGraph 上下文正确传播

        关键：interrupt() 必须在异步上下文中调用，不能通过 asyncio.to_thread，
        否则 LangGraph 的 ContextVar 无法正确传播，导致 interrupt 值丢失。
        """
        logger.info(f"shell_exec: 收到命令请求(async): {command[:200]}")

        # 1. 检查禁止命令
        blocked_reason = _is_command_blocked(command)
        if blocked_reason:
            logger.warning(f"shell_exec: 命令被禁止: {command[:100]} ({blocked_reason})")
            return StandardToolResult(
                content=f"命令已被安全策略拦截: {blocked_reason}\n命令: {command}\n此命令属于危险操作，不允许执行。",
                status=ToolStatus.ERROR,
                source="shell_exec",
                metadata={"command": command, "blocked": True, "reason": blocked_reason},
            ).to_tool_message()

        # 2. 白名单命令：在线程池中执行（不涉及 interrupt）
        if _is_command_whitelisted(command):
            logger.info(f"shell_exec: 白名单命令，直接执行: {command[:100]}")
            result = await asyncio.to_thread(_execute_command, command, timeout, working_dir)
            return result.to_tool_message()

        # 3. 非白名单命令：在异步上下文中调用 interrupt_for_approval
        logger.info(f"shell_exec: 非白名单命令，interrupt 等待用户确认: {command[:100]}")
        approval = interrupt_for_approval(
            tool_name="shell_exec",
            title="确认执行命令",
            description=f"Agent 请求执行以下非白名单命令，是否允许？",
            command=command,
            danger_level="high" if any(kw in command.lower() for kw in ["install", "remove", "delete", "format", "write"]) else "medium",
            extra={"timeout": timeout, "working_dir": working_dir},
        )

        # 4. 用户确认后 resume
        if approval is True:
            logger.info(f"shell_exec: 用户已确认，执行非白名单命令: {command[:100]}")
            result = await asyncio.to_thread(_execute_command, command, timeout, working_dir)
            return result.to_tool_message()
        else:
            logger.info(f"shell_exec: 用户已拒绝，不执行命令: {command[:100]}")
            return StandardToolResult(
                content=f"用户已拒绝执行此命令，命令未执行: {command}",
                status=ToolStatus.ERROR,
                source="shell_exec",
                metadata={"command": command, "rejected": True},
            ).to_tool_message()


# ── 工厂函数 ──────────────────────────────────────────────────────

def get_shell_exec_tools() -> list:
    """获取 Shell 执行工具列表"""
    return [ShellExecTool()]


# 单例实例
shell_exec = ShellExecTool()
