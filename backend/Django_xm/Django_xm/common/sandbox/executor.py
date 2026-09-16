"""沙箱执行器（Phase D 核心，沙箱加固版）

为 HIGH 级工具调用提供隔离执行环境。

执行策略（任务级开关 + 长驻容器 + fail-closed）：
    1. enable_sandbox=True 且全局 SANDBOX_ENABLED=True 且命令为 HIGH 级
       → 长驻 Docker 容器内执行（docker exec 复用）
    2. enable_sandbox=False（默认）或全局 SANDBOX_ENABLED=False
       → 本机执行（正常配置，非故障，不视为降级）
    3. 开启沙箱但 Docker 二次执行失败 → fail-closed 明确报错
       （含原因与"检查 Docker 或关闭沙箱重试"建议），绝不静默降级本机

容器生命周期（按 thread_id 长驻复用）：
    - 容器名：langchain-sandbox-{sha1(thread_id)[:12]}
    - 首次 docker run -d 创建：保留 --memory/--cpus/--network=none，
      挂载仅 DATA_DIR → /workspace（不再挂载整个项目根）
    - 后续 docker exec -w <容器工作目录> 复用执行
    - exec 失败（容器被删/已退出）→ docker rm -f + 重建一次重试；
      重建后仍失败 → 返回明确错误 SandboxResult（fail-closed）
    - cleanup_sandbox(thread_id)（docker rm -f）供 research_runner 在
      任务终态 / SESSION_TIMEOUT 超时后释放容器

与 shell_exec 集成：
    shell_exec._run 从工具调用上下文（config.configurable）读取
    enable_sandbox / thread_id，经 _execute_command 传给
    sandbox_executor.should_sandbox(command, enable_sandbox=...) 判定路由，
    返回 True 则走 execute(..., enable_sandbox=..., thread_id=...) 容器路径。
"""

import hashlib
import logging
import platform
import subprocess
import threading
import time
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

# 容器名前缀（按 thread_id 命名，长驻复用）
CONTAINER_NAME_PREFIX = "langchain-sandbox-"
# 容器内常驻 entrypoint：保持容器存活供 docker exec 复用
DEFAULT_CONTAINER_ENTRYPOINT = ("sleep", "infinity")
# 创建/删除容器的 CLI 超时（秒）
DOCKER_LIFECYCLE_TIMEOUT = 60

# docker CLI 自身错误退出码（守护进程/容器操作失败；
# 命令本身执行失败时 docker exec 透传命令的退出码，不在此列）
DOCKER_CLI_ERROR_RETURNCODE = 125

# docker exec 失败判定标记：命中任一即视为容器不可用（需自愈重建）
CONTAINER_ERROR_MARKERS = (
    "No such container",
    "is not running",
    "Error response from daemon",
    "Cannot connect to the Docker daemon",
)


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
    """检查沙箱是否启用（全局开关）。"""
    config = _get_sandbox_config()
    return config.get("ENABLED", False)


def _container_hash(thread_id: str) -> str:
    """由 thread_id 生成稳定的容器名后缀（sha256 前 12 位十六进制）。"""
    return hashlib.sha256(thread_id.encode("utf-8")).hexdigest()[:12]


def container_name(thread_id: str) -> str:
    """按 thread_id 生成长驻沙箱容器名。"""
    return f"{CONTAINER_NAME_PREFIX}{_container_hash(thread_id)}"


class SandboxExecutor:
    """沙箱执行器（模块级单例 sandbox_executor）。

    按 thread_id 维护长驻容器，命令经 docker exec 复用执行；
    容器损坏时自动重建一次，二次失败 fail-closed 报错。
    """

    def __init__(self):
        self._docker_available = None  # 延迟检测（首次执行时检测）
        self._lock = threading.RLock()
        # 本进程内已确认创建的容器（thread_id → 创建时间戳），用于复用判定与超时释放
        self._containers: dict[str, float] = {}

    def _ensure_docker_checked(self):
        """延迟检测 Docker 可用性（避免模块加载时阻塞）。"""
        if self._docker_available is None:
            self._docker_available = _is_docker_available()
            if self._docker_available:
                logger.info("[SandboxExecutor] Docker 可用，开启沙箱的任务将在容器内执行")
            else:
                logger.warning("[SandboxExecutor] Docker 不可用，开启沙箱的任务将无法在容器内执行")

    # ------------------------------------------------------------------
    # 路由判定
    # ------------------------------------------------------------------
    def should_sandbox(
        self,
        command: str,
        *,
        risk_level: str | None = None,
        enable_sandbox: bool = False,
    ) -> bool:
        """判断命令是否应在沙箱中执行（任务级判定）。

        判定逻辑（enable_sandbox 与 risk_level/high-risk 叠加）：
        1. enable_sandbox 必须为 True（任务级开关，默认关闭）
        2. 全局 SANDBOX_ENABLED 必须为 True
        3. risk_level == 'high' 或命令匹配 HIGH 级模式（双重保护）

        enable_sandbox=False 或全局未启用 → 返回 False（本机执行，非故障）；
        开启沙箱但 Docker 不可用的 fail-closed 错误由 execute() 承担。

        Args:
            command: 待执行的命令
            risk_level: 风险等级（safe/controlled/high），可选
            enable_sandbox: 任务级沙箱开关（来自工具调用上下文）

        Returns:
            True 表示应走沙箱执行路径
        """
        if not enable_sandbox or not _is_sandbox_enabled():
            return False

        # 风险等级判定：显式 HIGH 或命令匹配高风险模式
        is_high_risk = risk_level == "high" or _is_high_risk_command(command)
        return is_high_risk

    # ------------------------------------------------------------------
    # 执行入口
    # ------------------------------------------------------------------
    def execute(
        self,
        command: str,
        *,
        working_dir: str = "",
        timeout: int = DEFAULT_SANDBOX_TIMEOUT,
        risk_level: str | None = None,
        enable_sandbox: bool = False,
        thread_id: str = "",
    ) -> SandboxResult:
        """在沙箱中执行命令（未开启沙箱时走本机路径）。

        Args:
            command: 待执行的命令
            working_dir: 工作目录（宿主路径，映射到容器内 /workspace 下）
            timeout: 超时秒数
            risk_level: 风险等级
            enable_sandbox: 任务级沙箱开关
            thread_id: 研究任务/会话线程 ID（容器命名与生命周期定位依据）

        Returns:
            SandboxResult: 执行结果（fail-closed 时 error 含原因与建议）
        """
        # 任务级开关未开启或全局未启用 → 本机执行（正常配置，非故障）
        if not enable_sandbox or not _is_sandbox_enabled():
            return self._execute_local(command, working_dir, timeout)

        self._ensure_docker_checked()
        if not self._docker_available:
            # 开关开启但 Docker 运行时不可用：fail-closed 报错，不静默降级本机
            return self._fail_closed_error(
                "Docker 不可用，无法在沙箱中执行命令。请检查 Docker 服务，或关闭沙箱后重试。",
                command,
            )
        return self._execute_in_docker(command, working_dir, timeout, thread_id)

    # ------------------------------------------------------------------
    # 长驻容器 + docker exec
    # ------------------------------------------------------------------
    def _execute_in_docker(
        self,
        command: str,
        working_dir: str,
        timeout: int,
        thread_id: str,
    ) -> SandboxResult:
        """在长驻容器中执行命令（docker exec 复用，自愈重建一次重试）。"""
        container = container_name(thread_id)
        config = _get_sandbox_config()
        # 映射工作目录到容器路径（非 DATA_DIR 内路径回退到挂载根）
        container_workdir = map_to_container_path(working_dir) or CONTAINER_WORKSPACE

        with self._lock:
            # SESSION_TIMEOUT 超时后释放过期容器（下次执行自动重建）
            if thread_id in self._containers:
                session_timeout = int(config.get("SESSION_TIMEOUT", 3600))
                if time.monotonic() - self._containers[thread_id] > session_timeout:
                    logger.info(
                        f"[SandboxExecutor] 容器会话超时，释放重建: "
                        f"thread_id={thread_id}, container={container}"
                    )
                    self._remove_container(container)
                    self._containers.pop(thread_id, None)

            # 首次（或本进程重启后）创建长驻容器
            if thread_id not in self._containers:
                if not self._ensure_container(container):
                    return self._fail_closed_error(
                        f"沙箱容器创建失败（{container}）。请检查 Docker 服务，或关闭沙箱后重试。",
                        command,
                    )
                self._containers[thread_id] = time.monotonic()

        result = self._exec_in_container(container, container_workdir, command, timeout)

        # 容器损坏/被删/已退出：自愈（rm -f + 重建一次）后重试
        if self._is_container_unavailable(result):
            logger.warning(
                f"[SandboxExecutor] 容器执行失败，尝试重建一次重试: "
                f"thread_id={thread_id}, container={container}, "
                f"stderr={result.stderr.strip()[:200]}"
            )
            with self._lock:
                self._remove_container(container)
                self._containers.pop(thread_id, None)
                if not self._ensure_container(container):
                    return self._fail_closed_error(
                        f"沙箱容器重建失败（{container}）。请检查 Docker 服务，或关闭沙箱后重试。",
                        command,
                    )
                self._containers[thread_id] = time.monotonic()
            result = self._exec_in_container(container, container_workdir, command, timeout)
            if self._is_container_unavailable(result):
                # 二次失败：fail-closed 明确报错（绝不静默降级本机）
                return self._fail_closed_error(
                    "沙箱容器执行失败（已尝试重建一次）："
                    f"{result.stderr.strip() or result.error or '未知错误'}。"
                    "请检查 Docker 服务，或关闭沙箱后重试。",
                    command,
                )
        return result

    @staticmethod
    def _is_container_unavailable(result: SandboxResult) -> bool:
        """判定 docker exec 失败是否因容器不可用（vs 命令本身失败）。

        - docker CLI 自身错误（returncode=125）：守护进程/容器操作失败
        - stderr 命中容器错误标记：No such container / is not running 等
        命令本身失败时 docker exec 透传命令退出码，不触发重建。
        """
        if result.return_code == DOCKER_CLI_ERROR_RETURNCODE:
            return True
        return any(marker in result.stderr for marker in CONTAINER_ERROR_MARKERS)

    def _ensure_container(self, container: str) -> bool:
        """创建长驻沙箱容器（docker run -d，仅挂载 DATA_DIR）。

        Returns:
            bool: True 表示容器创建成功可复用
        """
        config = _get_sandbox_config()
        image = config.get("DOCKER_IMAGE", DEFAULT_DOCKER_IMAGE)
        memory_limit = config.get("MEMORY_LIMIT", DEFAULT_MEMORY_LIMIT)
        cpu_limit = config.get("CPU_LIMIT", DEFAULT_CPU_LIMIT)
        network_mode = config.get("NETWORK_MODE", DEFAULT_NETWORK_MODE)
        data_dir = str(settings.DATA_DIR)
        entrypoint, entrypoint_arg = DEFAULT_CONTAINER_ENTRYPOINT

        docker_cmd = [
            "docker",
            "run",
            "-d",
            "--name",
            container,
            "--memory",
            memory_limit,
            "--cpus",
            str(cpu_limit),
            "--network",
            network_mode,
            # 允许容器经 host.docker.internal 访问宿主机服务（如本机 Ollama:11435）。
            # host-gateway 自动解析到宿主机网关地址（Docker 20.10+，Windows/Mac/Linux 通用）；
            # 配合 SANDBOX_NETWORK=bridge 时容器可访问宿主机回环以外的服务。
            "--add-host",
            "host.docker.internal:host-gateway",
            "-v",
            f"{data_dir}:/workspace",
            "--entrypoint",
            entrypoint,
            image,
            entrypoint_arg,
        ]

        logger.info(
            f"[SandboxExecutor] 创建长驻沙箱容器: container={container}, "
            f"image={image}, mount={data_dir} -> /workspace"
        )
        try:
            # 参数均为配置常量（镜像/资源/挂载路径），非用户输入，执行隔离由 Docker 沙箱保障
            result = subprocess.run(  # noqa: S603, PLW1510
                docker_cmd,
                capture_output=True,
                text=True,
                timeout=DOCKER_LIFECYCLE_TIMEOUT,
                shell=(platform.system() == "Windows"),
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.warning(f"[SandboxExecutor] 容器创建异常: {container}, {e}")
            return False
        if result.returncode != 0:
            logger.warning(
                f"[SandboxExecutor] 容器创建失败: container={container}, "
                f"stderr={result.stderr.strip()[:300]}"
            )
            return False
        return True

    def _exec_in_container(
        self,
        container: str,
        container_workdir: str,
        command: str,
        timeout: int,
    ) -> SandboxResult:
        """在指定容器内执行命令（docker exec，复用长驻容器）。"""
        docker_cmd = [
            "docker",
            "exec",
            "-w",
            container_workdir,
            container,
            "bash",
            "-c",
            command,
        ]
        logger.info(
            f"[SandboxExecutor] Docker exec 沙箱执行: cmd={command[:100]}, "
            f"container={container}, workdir={container_workdir}, timeout={timeout}s"
        )
        try:
            # command 作为 docker exec 参数传入（非 shell 解释），执行隔离由 Docker 沙箱保障
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
            logger.warning(f"[SandboxExecutor] Docker exec 超时（{timeout}s）: {command[:100]}")
            return SandboxResult(
                stdout="",
                stderr=f"命令超时（{timeout}秒）",
                return_code=124,
                timed_out=True,
                sandboxed=True,
            )
        except Exception as e:
            # CLI 异常（如进程崩溃）：视为容器不可用，由自愈路径重建重试
            logger.warning(f"[SandboxExecutor] Docker exec 异常: {e}")
            return SandboxResult(
                stdout="",
                stderr=str(e),
                return_code=DOCKER_CLI_ERROR_RETURNCODE,
                sandboxed=True,
                error=str(e),
            )

    def _remove_container(self, container: str) -> None:
        """强制删除容器（best effort，docker rm -f）。"""
        try:
            subprocess.run(  # noqa: S603, PLW1510  # 容器名来自 container_name() 生成，非用户输入
                ["docker", "rm", "-f", container],  # noqa: S607  # docker 固定二进制名（PATH 解析）
                capture_output=True,
                text=True,
                timeout=DOCKER_LIFECYCLE_TIMEOUT,
                shell=(platform.system() == "Windows"),
            )
        except Exception as e:
            logger.debug(f"[SandboxExecutor] 删除容器失败（可忽略）: {container}, {e}")

    def cleanup_sandbox(self, thread_id: str) -> None:
        """删除 thread_id 对应的长驻沙箱容器（docker rm -f，best effort）。

        供 research_runner 在任务终态（成功/失败/取消）后调用，释放容器资源。

        Args:
            thread_id: 研究任务/会话线程 ID
        """
        container = container_name(thread_id)
        with self._lock:
            registered = thread_id in self._containers
            self._containers.pop(thread_id, None)
        self._remove_container(container)
        if registered:
            logger.info(f"[SandboxExecutor] 已清理沙箱容器: container={container}, thread_id={thread_id}")
        else:
            logger.debug(f"[SandboxExecutor] 容器未在本进程登记，跳过清理日志: container={container}, thread_id={thread_id}")

    # ------------------------------------------------------------------
    # fail-closed 错误与本机执行
    # ------------------------------------------------------------------
    def _fail_closed_error(self, reason: str, command: str) -> SandboxResult:
        """fail-closed：沙箱执行失败返回明确错误（绝不静默降级本机）。

        记录审计告警 + 沙箱失败指标（F2），命令视为执行失败返回给 agent。
        """
        logger.error(f"[SandboxExecutor] 沙箱执行失败（fail-closed）: {reason[:200]}")

        # 发布审计事件（F2 沙箱失败指标）
        try:
            from Django_xm.common.observability.approval_metrics import approval_metrics

            approval_metrics.on_sandbox_result(success=False)
        except Exception:
            # 指标记录失败不影响沙箱失败主流程
            logger.debug("记录沙箱失败指标失败")

        return SandboxResult(
            stdout="",
            stderr=reason,
            return_code=1,
            sandboxed=False,
            error=reason,
        )

    def _execute_local(
        self,
        command: str,
        working_dir: str,
        timeout: int,
    ) -> SandboxResult:
        """本机执行路径（任务级开关未开启或全局未启用时使用，非故障）。

        与历史行为一致：沙箱关闭时命令在本机执行，不做隔离也不视为降级。
        """
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
                # 本机执行 agent 命令（沙箱未开启时的设计使然），隔离由权限与审批保障
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


def cleanup_sandbox(thread_id: str) -> None:
    """模块级便捷函数：清理指定 thread_id 的沙箱容器（供 research_runner 调用）。"""
    sandbox_executor.cleanup_sandbox(thread_id)
