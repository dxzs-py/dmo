"""沙箱执行器（Phase D 核心）

为 HIGH 级工具调用提供隔离执行环境。

执行策略：
    1. SANDBOX_ENABLED=True + Docker 可用 → Docker 容器内执行
    2. SANDBOX_ENABLED=True + Docker 不可用 → 本机执行 + 审计告警（降级）
    3. SANDBOX_ENABLED=False → 本机执行（开发环境默认）

Docker 执行参数：
    - 镜像：SANDBOX_DOCKER_IMAGE（默认 python:3.11-slim）
    - 资源限制：--memory=512m --cpus=1 --network=none
    - 挂载：项目根目录 → /workspace
    - 工作目录：映射后的容器路径

与 shell_exec 集成：
    shell_exec._execute_command 在执行前调用 sandbox_executor.should_sandbox(command)，
    若返回 True 则走 execute_in_sandbox()，否则走原有本机执行路径。

与 file_writer 集成：
    HIGH 级文件写入操作（write_file/edit_file）同样可通过沙箱执行，
    路径映射由 path_mapper 处理。
"""

import logging
import platform
import subprocess
from dataclasses import dataclass

from django.conf import settings

from Django_xm.common.sandbox.path_mapper import CONTAINER_WORKSPACE, map_to_container_path

logger = logging.getLogger(__name__)

# 默认 Docker 镜像（含常用工具：bash/grep/find/python3）
DEFAULT_DOCKER_IMAGE = "python:3.11-slim"

# 默认资源限制
DEFAULT_MEMORY_LIMIT = "512m"
DEFAULT_CPU_LIMIT = 1
DEFAULT_NETWORK_MODE = "none"

# 默认超时（秒）
DEFAULT_SANDBOX_TIMEOUT = 120


@dataclass
class SandboxResult:
    """沙箱执行结果（与 StandardToolResult 字段对齐）。"""

    stdout: str = ""
    stderr: str = ""
    return_code: int = 0
    timed_out: bool = False
    sandboxed: bool = False  # 是否实际在沙箱中执行
    error: str = ""


def _get_sandbox_config() -> dict:
    """读取沙箱配置（从 settings.SANDBOX_CONFIG）。"""
    return getattr(settings, "SANDBOX_CONFIG", {})


def _is_docker_available() -> bool:
    """检查 Docker 是否可用（CLI 方式，无额外依赖）。"""
    try:
        # 固定探测命令（docker info），非用户输入；returncode 由调用方显式检查
        result = subprocess.run(  # noqa: PLW1510
            ["docker", "info", "--format", "{{.ServerVersion}}"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            shell=(platform.system() == "Windows"),
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.debug(f"[SandboxExecutor] Docker 不可用: {e}")
        return False


def _is_sandbox_enabled() -> bool:
    """检查沙箱是否启用。"""
    config = _get_sandbox_config()
    return config.get("ENABLED", False)


class SandboxExecutor:
    """沙箱执行器（模块级单例 sandbox_executor）。

    根据 SANDBOX_ENABLED 和 Docker 可用性选择执行策略：
    - Docker 可用 → 容器内执行
    - Docker 不可用 → 本机降级执行 + 审计告警
    """

    def __init__(self):
        self._docker_available = None  # 延迟检测（首次执行时检测）

    def _ensure_docker_checked(self):
        """延迟检测 Docker 可用性（避免模块加载时阻塞）。"""
        if self._docker_available is None:
            self._docker_available = _is_docker_available()
            if self._docker_available:
                logger.info("[SandboxExecutor] Docker 可用，HIGH 级操作将在沙箱中执行")
            else:
                logger.warning("[SandboxExecutor] Docker 不可用，HIGH 级操作将降级到本机执行")

    def should_sandbox(self, command: str, *, risk_level: str | None = None) -> bool:
        """判断命令是否应在沙箱中执行。

        判定逻辑：
        1. SANDBOX_ENABLED 必须为 True
        2. risk_level == 'high' 或命令匹配 HIGH 级模式（双重保护）
        3. Docker 可用（否则降级）

        Args:
            command: 待执行的命令
            risk_level: 风险等级（safe/controlled/high），可选

        Returns:
            True 表示应走沙箱执行路径
        """
        if not _is_sandbox_enabled():
            return False

        # 风险等级判定：显式 HIGH 或命令匹配高风险模式
        is_high_risk = risk_level == "high" or _is_high_risk_command(command)
        if not is_high_risk:
            return False

        self._ensure_docker_checked()
        # Docker 不可用时仍返回 True（走降级路径），但记录告警
        return True

    def execute(
        self,
        command: str,
        *,
        working_dir: str = "",
        timeout: int = DEFAULT_SANDBOX_TIMEOUT,
        risk_level: str | None = None,
    ) -> SandboxResult:
        """在沙箱中执行命令。

        Args:
            command: 待执行的命令
            working_dir: 工作目录（宿主路径）
            timeout: 超时秒数
            risk_level: 风险等级

        Returns:
            SandboxResult: 执行结果
        """
        self._ensure_docker_checked()

        if self._docker_available:
            return self._execute_in_docker(command, working_dir, timeout)
        else:
            return self._execute_local_fallback(command, working_dir, timeout)

    def _execute_in_docker(
        self,
        command: str,
        working_dir: str,
        timeout: int,
    ) -> SandboxResult:
        """在 Docker 容器中执行命令。"""
        config = _get_sandbox_config()
        image = config.get("DOCKER_IMAGE", DEFAULT_DOCKER_IMAGE)
        memory_limit = config.get("MEMORY_LIMIT", DEFAULT_MEMORY_LIMIT)
        cpu_limit = config.get("CPU_LIMIT", DEFAULT_CPU_LIMIT)
        network_mode = config.get("NETWORK_MODE", DEFAULT_NETWORK_MODE)
        project_root = str(settings.PROJECT_ROOT)

        # 映射工作目录到容器路径
        container_workdir = map_to_container_path(working_dir) or CONTAINER_WORKSPACE

        # 构建 docker run 命令
        docker_cmd = [
            "docker",
            "run",
            "--rm",
            "--memory",
            memory_limit,
            "--cpus",
            str(cpu_limit),
            "--network",
            network_mode,
            "-v",
            f"{project_root}:/workspace",
            "-w",
            container_workdir,
            "--entrypoint",
            "bash",
            image,
            "-c",
            command,
        ]

        logger.info(
            f"[SandboxExecutor] Docker 沙箱执行: cmd={command[:100]}, "
            f"image={image}, workdir={container_workdir}, timeout={timeout}s"
        )

        try:
            # command 作为 docker run 参数传入（非 shell 解释），执行隔离由 Docker 沙箱保障
            result = subprocess.run(  # noqa: S603, PLW1510
                docker_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=(platform.system() == "Windows"),
            )
            return SandboxResult(
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                return_code=result.returncode,
                sandboxed=True,
            )
        except subprocess.TimeoutExpired:
            logger.warning(f"[SandboxExecutor] Docker 执行超时（{timeout}s）: {command[:100]}")
            return SandboxResult(
                stdout="",
                stderr=f"命令超时（{timeout}秒）",
                return_code=124,
                timed_out=True,
                sandboxed=True,
            )
        except Exception:
            logger.exception("[SandboxExecutor] Docker 执行异常")
            # Docker 执行失败，降级到本机
            return self._execute_local_fallback(command, working_dir, timeout)

    def _execute_local_fallback(
        self,
        command: str,
        working_dir: str,
        timeout: int,
    ) -> SandboxResult:
        """本机降级执行（Docker 不可用时）。

        记录审计告警，仍在本机执行命令（不阻塞用户操作）。
        """
        # 审计告警：HIGH 级操作在本机执行
        logger.warning(
            f"[SandboxExecutor] 降级执行（Docker 不可用）: cmd={command[:100]}, "
            f"workdir={working_dir} — HIGH 级操作未在沙箱中隔离"
        )

        # 发布审计事件（F2 沙箱失败指标）
        try:
            from Django_xm.common.observability.approval_metrics import approval_metrics

            approval_metrics.on_sandbox_result(success=False)
        except Exception:
            # 指标记录失败不影响沙箱降级执行主流程
            logger.debug("记录沙箱失败指标失败")

        is_windows = platform.system() == "Windows"
        try:
            popen_kwargs = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "cwd": working_dir or None,
                "encoding": "utf-8",
                "errors": "replace",
            }
            if is_windows:
                # 本机降级执行 agent 命令（沙箱不可用时的设计使然），隔离由权限与审批保障
                process = subprocess.Popen(command, shell=True, **popen_kwargs)  # noqa: S602
            else:
                process = subprocess.Popen(  # noqa: S603
                    ["bash", "-c", command],  # noqa: S607
                    start_new_session=True,
                    **popen_kwargs,
                )
            stdout, stderr = process.communicate(timeout=timeout)
            return SandboxResult(
                stdout=stdout or "",
                stderr=stderr or "",
                return_code=process.returncode,
                sandboxed=False,
            )
        except subprocess.TimeoutExpired:
            process.kill()
            return SandboxResult(
                stdout="",
                stderr=f"命令超时（{timeout}秒）",
                return_code=124,
                timed_out=True,
                sandboxed=False,
            )
        except Exception as e:
            return SandboxResult(
                stdout="",
                stderr=str(e),
                return_code=1,
                sandboxed=False,
                error=str(e),
            )


# HIGH 级命令模式（与 policies.ShellExecApprovalPolicy.HIGH_RISK_KEYWORDS 对齐）
_HIGH_RISK_PATTERNS = [
    "rm -rf",
    "rm -r ",
    "rmdir",
    "del /f",
    "del /s",
    "format ",
    "shutdown",
    "mkfs",
    "dd if=",
    "> /dev/sd",
    "chmod -R 777",
    "curl | sh",
    "curl | bash",
    "wget | sh",
    "wget | bash",
    ":(){:|:&};:",  # fork bomb
    "reg delete",
    "reg add",
]


def _is_high_risk_command(command: str) -> bool:
    """简单 HIGH 级命令检测（双重保护，与 policy.assess_risk 独立）。

    即使 policy 判定为 CONTROLLED，如果命令包含高危模式，
    沙箱路由仍会将其路由到容器内执行。

    Args:
        command: 待检测的命令

    Returns:
        True 表示命令匹配 HIGH 级模式
    """
    cmd_lower = command.lower().strip()
    return any(pattern in cmd_lower for pattern in _HIGH_RISK_PATTERNS)


# 模块级单例
sandbox_executor = SandboxExecutor()
