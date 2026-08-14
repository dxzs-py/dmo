"""Shell 命令执行工具

提供安全的 Shell 命令执行能力，支持：
- 命令白名单：白名单内命令直接执行
- 危险命令拦截：禁止执行 rm -rf、format、shutdown 等
- 用户确认机制：非白名单命令通过 LangGraph interrupt 暂停，等待用户确认后 resume
- 超时控制：防止命令挂起
- 输出截断：防止返回内容过大
- 工作目录限制：限制命令执行范围
"""

import logging
import os
import platform
import re
import signal
import subprocess
from pathlib import Path

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from Django_xm.apps.tools.base import AsyncToolMixin
from Django_xm.apps.tools.errors import TOOL_VERSION, StandardToolResult, ToolStatus

logger = logging.getLogger(__name__)


# ── 安全配置 ──────────────────────────────────────────────────────

# 白名单命令前缀（这些命令可直接执行，无需用户确认）
# 注意：部分命令在 Windows 上不可用，_get_whitelist_commands 会根据平台过滤
_UNIX_ONLY_COMMANDS = {"ls", "cat", "head", "tail", "wc", "find", "which"}
_WINDOWS_ONLY_COMMANDS = {"dir", "type", "where"}

DEFAULT_WHITELIST_COMMANDS: list[str] = [
    # 浏览器自动化
    "agent-browser",
    # 文档转换
    "pandoc",
    # Python 高频安全脚本（仅限 manage.py / pytest，其他 python 调用需审批）
    "python manage.py",
    "python manage.py help",
    "python -m pytest",
    "python -m unittest",
    "python3 manage.py",
    "python3 manage.py help",
    "python3 -m pytest",
    "python3 -m unittest",
    # pip 只读子命令
    "pip list",
    "pip show",
    "pip check",
    "pip3 list",
    "pip3 show",
    "pip3 check",
    # Node 包管理只读子命令（node/npx 本体已移除，需审批）
    "npm list",
    "npm view",
    "npm info",
    # 系统信息（只读）— 跨平台
    "echo",
    "whoami",
    "hostname",
    # 系统信息（只读）— Windows
    "dir",
    "type",
    "where",
    # 系统信息（只读）— Unix
    "ls",
    "cat",
    "head",
    "tail",
    "wc",
    "find",
    "which",
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
BLOCKED_PATTERNS: list[str] = [
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
    # Windows 特有危险命令
    r"\bdiskpart\b",
    r"\bbcdedit\b",
    r"\breg\s+(delete|add)\b",
    r"\bnet\s+user\b",
    r"\bnet\s+localgroup\b",
    r"\bpowershell\s+-enc\b",
    r"\bpowershell\s+-e\b",
]

# 最大输出长度（字符）
MAX_OUTPUT_LENGTH = 50000

# 默认超时（秒）
DEFAULT_TIMEOUT = 60

# 最大超时（秒）
MAX_TIMEOUT = 300

# 交互式命令超时（秒）——这些命令启动后可能不会自动退出
INTERACTIVE_COMMAND_TIMEOUT = 30

# 交互式命令前缀（启动后不自动退出的 CLI 工具）
INTERACTIVE_COMMAND_PREFIXES: list[str] = [
    "agent-browser",  # 浏览器自动化，open/snapshot 等子命令启动后进程不退出
]


def _get_effective_timeout(command: str, requested_timeout: int) -> int:
    """根据命令类型返回有效超时时间

    交互式命令（如 agent-browser）启动后进程不会自动退出，
    使用较短的超时避免长时间阻塞。
    """
    cmd_first_word = command.strip().split()[0] if command.strip() else ""
    for prefix in INTERACTIVE_COMMAND_PREFIXES:
        if cmd_first_word == prefix:
            return min(INTERACTIVE_COMMAND_TIMEOUT, requested_timeout)
    return requested_timeout


def _kill_process_tree(process: subprocess.Popen, is_windows: bool) -> None:
    """终止进程树（包括所有子进程），防止僵尸进程

    Windows 下 shell=True 启动的进程通过 cmd.exe 中转，
    subprocess.kill() 只杀 cmd.exe，子进程仍存活并持有管道，
    导致 communicate() 无法返回。必须用 taskkill /F /T 杀整个进程树。
    """
    try:
        if is_windows:
            # Windows: taskkill /F /T 强制终止整个进程树
            subprocess.run(  # noqa: S602, PLW1510  # taskkill 清理 PID 来自 Popen 对象，非用户输入；check=False 设计使然
                f"taskkill /F /T /PID {process.pid}",
                shell=True,
                capture_output=True,
                timeout=5,
            )
        else:
            # Unix: 向进程组发送 SIGTERM
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)  # type: ignore[attr-defined]  # POSIX-only, gracefully skipped on Windows
            except (ProcessLookupError, PermissionError, OSError):
                pass
    except Exception:  # noqa: S110  # cleanup, 进程终止失败不应影响调用方
        pass
    finally:
        # 兜底：确保主进程被终止
        try:
            process.kill()
        except (OSError, ProcessLookupError):
            pass


def _get_whitelist_commands() -> list[str]:
    """获取白名单命令列表（支持 Django settings 覆盖，根据平台过滤）"""
    try:
        from django.conf import settings as django_settings

        custom = getattr(django_settings, "SHELL_EXEC_WHITELIST", None)
        if custom is not None:
            return list(custom)
    except (ImportError, AttributeError):
        pass

    # 根据平台过滤：Windows 上移除 Unix 特有命令，Unix 上移除 Windows 特有命令
    is_windows = platform.system() == "Windows"
    filtered = []
    for cmd in DEFAULT_WHITELIST_COMMANDS:
        first_word = cmd.split()[0]
        if is_windows and first_word in _UNIX_ONLY_COMMANDS:
            continue
        if not is_windows and first_word in _WINDOWS_ONLY_COMMANDS:
            continue
        filtered.append(cmd)
    return filtered


def _is_command_blocked(command: str) -> str | None:
    """检查命令是否匹配禁止模式，返回匹配的模式描述或 None

    使用 re.DOTALL 标志使 '.' 匹配换行符，覆盖跨行命令拼接攻击
    （如 "echo ok\\nrm -rf /" 通过换行符绕过单行正则匹配）。
    """
    cmd_lower = command.lower().strip()
    for pattern in BLOCKED_PATTERNS:
        # re.DOTALL: 使 . 匹配换行符，防止跨行命令拼接绕过
        if re.search(pattern, cmd_lower, re.IGNORECASE | re.DOTALL):
            return f"匹配禁止模式: {pattern}"
    return None


# 重定向/管道操作符检测模式：单词白名单命令携带这些操作符时需走审批
# 防止 "echo bad > /etc/passwd" / "cat file | rm -rf /" 等绕过攻击
_REDIRECT_PIPE_PATTERN = re.compile(
    r"(?<!\w)(?:"
    r">{1,2}|<{1,2}|"  # 重定向: > >> < <<
    r"\|"  # 管道: |
    r")(?:\s|$)",
    re.DOTALL,
)


def _has_redirect_or_pipe(command: str) -> bool:
    """检测命令是否包含重定向或管道操作符

    单词白名单命令（如 echo/pandoc）携带重定向/管道时可执行任意后续命令，
    必须强制走审批流程，不允许直接执行。

    Returns:
        True 表示命令包含重定向/管道操作符
    """
    return bool(_REDIRECT_PIPE_PATTERN.search(command))


def _is_command_whitelisted(command: str) -> bool:
    """检查命令是否在白名单中

    匹配规则：
    - 多词白名单命令（如 "git status"）：完整前缀匹配
    - 单词白名单命令（如 "pandoc"）：分词后第一个词匹配，且命令不含重定向/管道
      操作符（携带 > >> < << | 时强制走审批，防止 "echo bad > /etc/passwd" 等
      绕过攻击）
    """
    whitelist = _get_whitelist_commands()
    cmd_stripped = command.strip()

    if not cmd_stripped:
        return False

    # 精确匹配
    for wl_cmd in whitelist:
        if cmd_stripped == wl_cmd:
            # 单词命令精确匹配时仍需检查重定向/管道
            # （虽无参数，但保持一致性）
            return not (" " not in wl_cmd and _has_redirect_or_pipe(cmd_stripped))

    cmd_first_word = cmd_stripped.split()[0] if cmd_stripped.split() else ""
    for wl_cmd in whitelist:
        wl_first_word = wl_cmd.split()[0]
        # 白名单命令本身包含空格（如 "git status"），需要完整前缀匹配
        if " " in wl_cmd:
            if cmd_stripped.startswith(wl_cmd + " ") or cmd_stripped == wl_cmd:
                # 多词白名单命令也检查重定向/管道，防止
                # "git status > /etc/passwd" 等绕过
                return not _has_redirect_or_pipe(cmd_stripped)
        # 单词白名单命令：仅当第一个词匹配且无重定向/管道时放行
        elif cmd_first_word == wl_first_word:
            # 携带重定向/管道的单词命令必须走审批
            return not _has_redirect_or_pipe(cmd_stripped)

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


def _adapt_command_for_platform(command: str) -> str:
    """适配命令到当前平台（Windows 兼容性处理）

    Windows 的常见兼容性问题：
    - mkdir -p <path>: Windows mkdir 默认递归创建父目录，-p 会被当作目录名
    - mkdir --parents <path>: 同上，GNU 长选项也不支持
    - rm -rf / rm -r / rm -f: Windows 没有 rm，但 Git Bash 等环境可能有
    - cp -r: Windows 没有 cp -r
    - touch: Windows 没有 touch
    - ls: Windows 没有 ls（有 dir）
    - cat: Windows 没有 cat（有 type）
    - grep: Windows 没有原生 grep
    """
    if platform.system() != "Windows":
        return command

    adapted = command

    # mkdir -p <path> → mkdir <path>（Windows mkdir 默认递归创建）
    adapted = re.sub(r"\bmkdir\s+-p\s+", "mkdir ", adapted)
    # mkdir --parents <path> → mkdir <path>
    adapted = re.sub(r"\bmkdir\s+--parents\s+", "mkdir ", adapted)

    # rm -rf <path> → rmdir /s /q <path>（目录）或 del /f /q <path>（文件）
    # 注意：rm -rf 是危险操作，通常会被 BLOCKED_PATTERNS 拦截，
    # 这里只处理 rm -r（不带 -f）的情况
    adapted = re.sub(r"\brm\s+-r\s+", "rmdir /s /q ", adapted)

    # cp -r <src> <dst> → xcopy /e /i <src> <dst>
    adapted = re.sub(r"\bcp\s+-r\s+", "xcopy /e /i ", adapted)

    # touch <file> → type nul > <file>（Windows 创建空文件）
    adapted = re.sub(r"\btouch\s+", "type nul > ", adapted)

    # ls <args> → dir <args>（简单替换，不处理复杂参数）
    adapted = re.sub(r"\bls\s+-la\b", "dir /a", adapted)
    adapted = re.sub(r"\bls\s+-l\b", "dir", adapted)
    adapted = re.sub(r"\bls\s+-a\b", "dir /a", adapted)
    adapted = re.sub(r"\bls\b", "dir", adapted)

    # cat <file> → type <file>
    adapted = re.sub(r"\bcat\s+", "type ", adapted)

    # grep <pattern> <file> → findstr <pattern> <file>（简单替换）
    adapted = re.sub(r"\bgrep\s+-r\s+", "findstr /s ", adapted)
    adapted = re.sub(r"\bgrep\s+-i\s+", "findstr /i ", adapted)
    adapted = re.sub(r"\bgrep\s+", "findstr ", adapted)

    # which <cmd> → where <cmd>
    adapted = re.sub(r"\bwhich\s+", "where ", adapted)

    # clear → cls
    adapted = re.sub(r"\bclear\b", "cls", adapted)

    return adapted


def _sandbox_result_to_standard(sandbox_result, command: str) -> StandardToolResult:
    """将 SandboxResult 转换为 StandardToolResult（保持输出格式一致）。

    Phase D：沙箱执行结果与本机执行结果统一格式，上层工具无感知差异。
    """
    output_parts = []
    if sandbox_result.stdout.strip():
        output_parts.append(sandbox_result.stdout.strip())
    if sandbox_result.stderr.strip():
        output_parts.append(f"[stderr]\n{sandbox_result.stderr.strip()}")

    output = "\n\n".join(output_parts) if output_parts else "(命令执行完成，无输出)"
    sandbox_tag = " [沙箱执行]" if sandbox_result.sandboxed else " [沙箱降级-本机执行]"

    if sandbox_result.timed_out:
        return StandardToolResult(
            content=f"命令执行超时{sandbox_tag}。命令: {command}\n已捕获输出:\n{output}",
            status=ToolStatus.ERROR,
            source="shell_exec",
            metadata={
                "timeout": True,
                "command": command,
                "sandboxed": sandbox_result.sandboxed,
            },
        )

    if sandbox_result.return_code != 0:
        return StandardToolResult(
            content=f"命令退出码: {sandbox_result.return_code}{sandbox_tag}\n{output}",
            status=ToolStatus.ERROR,
            source="shell_exec",
            metadata={
                "return_code": sandbox_result.return_code,
                "command": command,
                "sandboxed": sandbox_result.sandboxed,
            },
        )

    return StandardToolResult(
        content=output,
        status=ToolStatus.SUCCESS,
        source="shell_exec",
        metadata={
            "return_code": sandbox_result.return_code,
            "command": command,
            "sandboxed": sandbox_result.sandboxed,
        },
    )


def _execute_command(
    command: str,
    timeout: int = DEFAULT_TIMEOUT,
    working_dir: str = "",
) -> StandardToolResult:
    """执行 Shell 命令并返回结果

    Phase D 集成：HIGH 级命令通过沙箱执行器路由。
    - sandbox_executor.should_sandbox() → True → Docker 容器内执行（或降级本机）
    - 否则 → 本机直接执行（原有逻辑）

    使用 Popen + communicate(timeout) 替代 subprocess.run，
    超时后通过 _kill_process_tree 终止整个进程树，
    解决 Windows shell=True 下子进程不被杀导致管道挂起的问题。
    """
    # Phase D: HIGH 级命令沙箱路由
    try:
        from Django_xm.common.sandbox import sandbox_executor

        if sandbox_executor.should_sandbox(command):
            effective_timeout = min(_get_effective_timeout(command, timeout), MAX_TIMEOUT)
            safe_cwd = _get_safe_working_dir(working_dir)
            sandbox_result = sandbox_executor.execute(
                command,
                working_dir=safe_cwd,
                timeout=effective_timeout,
            )
            # 记录沙箱执行指标（F2）
            try:
                from Django_xm.common.observability.approval_metrics import approval_metrics

                approval_metrics.on_sandbox_result(success=(sandbox_result.return_code == 0))
            except Exception:
                # 指标记录失败不影响沙箱执行主流程
                logger.debug("记录沙箱执行指标失败")
            return _sandbox_result_to_standard(sandbox_result, command)
    except ImportError:
        pass  # 沙箱模块不可用，走本机执行
    except Exception as sandbox_err:
        logger.warning(f"shell_exec: 沙箱路由异常(降级本机): {sandbox_err}")

    effective_timeout = _get_effective_timeout(command, timeout)
    effective_timeout = min(effective_timeout, MAX_TIMEOUT)
    cwd = _get_safe_working_dir(working_dir)
    is_windows = platform.system() == "Windows"
    timed_out = False

    # 平台适配：修正 Windows 不兼容的命令参数（如 mkdir -p）
    command = _adapt_command_for_platform(command)

    try:
        popen_kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "cwd": cwd,
            "encoding": "utf-8",
            "errors": "replace",
        }
        if is_windows:
            # 命令执行工具设计使然（agent shell 工具）：命令经白名单/禁止模式校验，隔离由沙箱与权限保障
            process = subprocess.Popen(command, shell=True, **popen_kwargs)  # noqa: S602
        else:
            process = subprocess.Popen(  # noqa: S603
                ["bash", "-c", command],  # noqa: S607
                start_new_session=True,  # 创建新进程组，便于 killpg
                **popen_kwargs,
            )

        try:
            stdout, stderr = process.communicate(timeout=effective_timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            logger.warning(f"shell_exec: 命令超时（{effective_timeout}s），终止进程树: {command[:100]}")
            _kill_process_tree(process, is_windows)
            # 终止后再尝试读取已缓冲的输出
            try:
                stdout, stderr = process.communicate(timeout=5)
            except (subprocess.TimeoutExpired, Exception):
                stdout, stderr = "", ""

        stdout = stdout or ""
        stderr = stderr or ""

        # 截断输出
        if len(stdout) > MAX_OUTPUT_LENGTH:
            stdout = stdout[:MAX_OUTPUT_LENGTH] + f"\n... [输出已截断，共 {len(stdout)} 字符]"
        if len(stderr) > MAX_OUTPUT_LENGTH // 4:
            stderr = stderr[: MAX_OUTPUT_LENGTH // 4] + "\n... [错误输出已截断]"

        output_parts = []
        if stdout.strip():
            output_parts.append(stdout.strip())
        if stderr.strip():
            output_parts.append(f"[stderr]\n{stderr.strip()}")

        output = "\n\n".join(output_parts) if output_parts else "(命令执行完成，无输出)"

        if timed_out:
            return StandardToolResult(
                content=f"命令执行超时（{effective_timeout}秒），已终止。命令: {command}\n已捕获输出:\n{output}",
                status=ToolStatus.ERROR,
                source="shell_exec",
                metadata={"timeout": effective_timeout, "command": command, "timed_out": True},
            )

        if process.returncode != 0:
            return StandardToolResult(
                content=f"命令退出码: {process.returncode}\n{output}",
                status=ToolStatus.ERROR,
                source="shell_exec",
                metadata={"return_code": process.returncode, "command": command},
            )

        return StandardToolResult(
            content=output,
            status=ToolStatus.SUCCESS,
            source="shell_exec",
            metadata={"return_code": process.returncode, "command": command},
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

    command: str = Field(description="要执行的 Shell 命令")
    timeout: int = Field(default=DEFAULT_TIMEOUT, description=f"命令执行超时时间（秒），最大 {MAX_TIMEOUT} 秒")
    working_dir: str = Field(default="", description="命令执行的工作目录，为空则使用当前目录")


class ShellExecTool(AsyncToolMixin, BaseTool):
    """Shell 命令执行工具

    安全策略：
    1. 禁止命令：始终拒绝执行（由 _is_command_blocked 检查）
    2. 审批：由 ApprovalMiddleware 在 after_model 钩子统一处理
       （非白名单命令触发审批，白名单/黑名单命令不拦截）
    3. 超时控制：防止命令挂起
    4. 输出截断：防止返回内容过大

    工具层不参与审批判断，到达 _run 的命令已通过审批（或无需审批）。
    """

    name: str = "shell_exec"
    version: str = TOOL_VERSION
    metadata: dict = Field(
        default_factory=lambda: {"tier": "extended", "visibility": "selectable", "category": "system"}
    )
    description: str = (
        "执行 Shell 命令并返回输出结果。"
        "适用场景：需要运行命令行工具（如 agent-browser、pandoc、python manage.py 等）、查看系统信息、执行自动化任务。"
        "不适用：文件读写（应使用 fs_read_file/fs_write_file）、网络搜索（应使用 web_search）。"
        "参数：command-要执行的命令（必填），timeout-超时秒数（默认60），working_dir-工作目录（默认当前目录）。"
        "安全策略：白名单命令（agent-browser、pandoc、python manage.py、git status 等）直接执行；"
        "非白名单命令（含 python/node/npx 等可执行任意代码的解释器）需走审批流程；"
        "携带重定向/管道的命令强制走审批（防止 echo bad > /etc/passwd 等绕过）；"
        "危险命令（rm -rf、shutdown 等）始终拒绝。"
    )
    args_schema: type[BaseModel] = ShellExecInput

    def _run(
        self,
        command: str,
        timeout: int = DEFAULT_TIMEOUT,
        working_dir: str = "",
    ) -> str:
        """执行 Shell 命令

        审批由 ApprovalMiddleware 在 after_model 钩子统一处理，
        工具层不参与审批判断。到达此方法的命令已通过审批或无需审批。
        """
        # 参数校验：防止 LLM 传入空参数导致不可预期的行为
        if not command or not command.strip():
            logger.warning("shell_exec: 收到空命令，拒绝执行")
            return StandardToolResult(
                content="命令不能为空，请提供要执行的 Shell 命令。",
                status=ToolStatus.ERROR,
                source="shell_exec",
                metadata={"command": command, "empty_command": True},
            ).to_tool_message()

        logger.info(f"shell_exec: 收到命令请求: {command[:200]}")

        # 检查禁止命令（黑名单命令始终拒绝，不由审批处理）
        blocked_reason = _is_command_blocked(command)
        if blocked_reason:
            logger.warning(f"shell_exec: 命令被禁止: {command[:100]} ({blocked_reason})")
            return StandardToolResult(
                content=f"命令已被安全策略拦截: {blocked_reason}\n命令: {command}\n此命令属于危险操作，不允许执行。",
                status=ToolStatus.ERROR,
                source="shell_exec",
                metadata={"command": command, "blocked": True, "reason": blocked_reason},
            ).to_tool_message()

        # 执行命令（审批已由 ApprovalMiddleware 统一处理）
        logger.info(f"shell_exec: 执行命令: {command[:100]}")
        result = _execute_command(command, timeout, working_dir)
        return result.to_tool_message()


# ── 工厂函数 ──────────────────────────────────────────────────────


def get_shell_exec_tools() -> list:
    """获取 Shell 执行工具列表"""
    return [ShellExecTool()]


# 单例实例
shell_exec = ShellExecTool()
