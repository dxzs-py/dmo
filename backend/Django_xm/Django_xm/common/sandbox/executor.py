"""沙箱执行器（Phase D 核心，沙箱加固版）

为 HIGH 级工具调用提供隔离执行环境。

执行策略（risk_level 阶梯 + 任务级开关 + 长驻容器 + fail-closed）：
    1. 全局 SANDBOX_ENABLED=False → 本机执行（既有语义：全局关=不沙箱）
    2. HIGH 级（risk_level='high' 或命令命中高危子串）→ 强制容器执行：
       任务级开关不参与；Docker 不可用/容器失败 → fail-closed 明确报错，
       绝不静默降级本机（S-02：HIGH 命令绝不回落宿主）
    3. 任务级开关 enable_sandbox=True + 受控级（controlled）→ 容器执行
    4. 其余（任务开关关闭 / 低风险 safe）→ 本机执行（正常配置，非故障）

容器生命周期（按 thread_id 长驻复用）：
    - 容器名：langchain-sandbox-{sha1(thread_id)[:12]}
    - 首次 docker run -d 创建：保留 --memory/--cpus/--network=none，
      挂载仅 DATA_DIR → /workspace（不再挂载整个项目根）
    - 后续 docker exec -w <容器工作目录> 复用执行
    - exec 失败（容器被删/已退出）→ docker rm -f + 重建一次重试；
      重建后仍失败 → 返回明确错误 SandboxResult（fail-closed）
    - docker run 遇同名容器（其他 worker 进程创建 / 上次进程崩溃残留）
      → 收编复用：inspect 状态为 running 直接复用，exited 删除重建一次；
      容器不存在（run 因其他原因失败）→ 维持 fail-closed（创建操作幂等）
    - cleanup_sandbox(thread_id)（docker rm -f）供 research_runner 在
      任务终态 / SESSION_TIMEOUT 超时后释放容器

与 shell_exec 集成：
    shell_exec._run 复用审批策略评估 risk_level，连同工具调用上下文
    （config.configurable）中的 enable_sandbox / thread_id，经 _execute_command
    传给 sandbox_executor.should_sandbox(command, risk_level=..., enable_sandbox=...)
    判定路由，返回 True 则走 execute(..., enable_sandbox=True, thread_id=...)
    容器路径（路由已判定进沙箱，任务开关以路由结果为准）。
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

# docker inspect 返回的容器状态（收编跨进程/重启残留容器时判定）
_CONTAINER_RUNNING_STATE = "running"
_CONTAINER_EXITED_STATE = "exited"


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
        """判断命令是否应在沙箱中执行（fail-closed 路由阶梯，S-02）。

        路由阶梯（按序判定）：
        1. 全局 SANDBOX_ENABLED=False → False（本机执行；既有语义：全局关=不沙箱）
        2. HIGH 级（risk_level == 'high' 或命令命中高危子串模式）→ True
           强制沙箱：任务级开关不参与判定（审批承诺"强制 Docker 沙箱执行"）；
           Docker 不可用时由 execute() fail-closed 拒绝执行，绝不回落宿主
        3. 任务级开关 enable_sandbox=False（且非 HIGH）→ False（本机执行，非故障）
        4. 任务开关开启时按等级路由：
           - risk_level == 'controlled' → True（进容器；Docker 不可用由 execute() fail-closed）
           - risk_level == 'safe' → False（低风险本机，高危子串已在第 2 步兜底）
           - risk_level 为 None（调用方未分类，兼容直调）→ False（仅第 2 步子串兜底）

        Args:
            command: 待执行的命令
            risk_level: 审批策略判定的风险等级
                （RiskLevel 值：safe/controlled/high，兼容枚举与字符串）
            enable_sandbox: 任务级沙箱开关（来自工具调用上下文）

        Returns:
            True 表示必须走沙箱执行路径
        """
        if not _is_sandbox_enabled():
            return False

        # HIGH 级：显式等级或高危子串命中 → 强制沙箱（任务级开关不参与）
        is_high_risk = risk_level == "high" or _is_high_risk_command(command)
        if is_high_risk:
            return True

        if not enable_sandbox:
            return False

        # 任务开关开启：受控级进容器；safe/None 保持本机（高危子串已兜底）
        return risk_level == "controlled"

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
        """在沙箱中执行命令（未路由进沙箱时走本机路径）。

        Args:
            command: 待执行的命令
            working_dir: 工作目录（宿主路径，映射到容器内 /workspace 下）
            timeout: 超时秒数
            risk_level: 审批策略判定的风险等级（safe/controlled/high）
            enable_sandbox: 任务级沙箱开关
            thread_id: 研究任务/会话线程 ID（容器命名与生命周期定位依据）

        Returns:
            SandboxResult: 执行结果（fail-closed 时 error 含原因与建议）
        """
        # 全局未启用 → 本机执行（既有语义：全局关=不沙箱，非故障）
        if not _is_sandbox_enabled():
            return self._execute_local(command, working_dir, timeout)

        # HIGH 级（显式 risk_level 或高危子串）强制沙箱：任务级开关不参与（S-02），
        # Docker 不可用时 fail-closed 报错，绝不静默降级本机
        is_high_risk = risk_level == "high" or _is_high_risk_command(command)
        if not enable_sandbox and not is_high_risk:
            return self._execute_local(command, working_dir, timeout)

        self._ensure_docker_checked()
        if not self._docker_available:
            # 开关开启但 Docker 运行时不可用：fail-closed 报错，不静默降级本机
            result = self._fail_closed_error(
                "Docker 不可用，无法在沙箱中执行命令。请检查 Docker 服务，或关闭沙箱后重试。",
                command,
            )
        else:
            result = self._execute_in_docker(command, working_dir, timeout, thread_id)

        # 沙箱指标统一在 execute 出口记录（单一数据源），避免 fail-closed 路径被
        # _fail_closed_error 内部与调用方（shell.py）重复计数（双 +1 失真）。
        try:
            from Django_xm.common.observability.approval_metrics import approval_metrics
            approval_metrics.on_sandbox_result(success=(result.return_code == 0))
        except Exception:
            logger.debug("记录沙箱执行指标失败")
        return result

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
        """确保长驻沙箱容器可用（创建或收编，幂等）。

        docker run 失败时的典型原因：同名容器已存在——其他 worker 进程创建，
        或上次进程崩溃未清理的残留（本进程登记表无记录）。此时不直接失败，
        而是收编已存在容器：
        - 容器运行中 → 直接复用（跨进程安全）
        - 容器已退出 → docker rm -f 删除后重建一次
        - 容器不存在（run 因其他原因失败，如镜像拉取失败）→ 维持 fail-closed

        Returns:
            bool: True 表示容器可用（新建或收编）
        """
        if self._run_container(container):
            return True
        return self._adopt_existing_container(container)

    def _run_container(self, container: str) -> bool:
        """执行 docker run -d 创建长驻沙箱容器（仅挂载 DATA_DIR）。

        Returns:
            bool: True 表示容器创建成功
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

    def _adopt_existing_container(self, container: str) -> bool:
        """收编同名已存在容器（跨进程部署 / 进程重启后的幂等保障）。

        Returns:
            bool: True 表示已存在容器可复用（或已重建成功）
        """
        state = self._inspect_container_state(container)
        if state == _CONTAINER_RUNNING_STATE:
            logger.info(
                f"[SandboxExecutor] 容器已存在且运行中，收编复用: container={container}"
            )
            return True
        if state == _CONTAINER_EXITED_STATE:
            logger.info(
                f"[SandboxExecutor] 同名容器已退出，删除重建: container={container}"
            )
            self._remove_container(container)
            return self._run_container(container)
        # 容器不存在或状态未知：run 失败是真实原因（如镜像拉取失败），维持 fail-closed
        return False

    def _inspect_container_state(self, container: str) -> str | None:
        """查询容器状态（docker inspect -f {{.State.Status}}）。

        Returns:
            容器状态字符串（running/exited/...）；容器不存在或查询失败返回 None。
        """
        try:
            result = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Status}}", container],
                capture_output=True,
                text=True,
                timeout=DOCKER_LIFECYCLE_TIMEOUT,
                shell=(platform.system() == "Windows"),
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

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

        仅返回失败结果；沙箱指标统一在 execute() 出口按 return_code 记录，避免重复计数。
        """
        logger.error(f"[SandboxExecutor] 沙箱执行失败（fail-closed）: {reason[:200]}")

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


# ── HIGH 级命令权威词表（单一真源，B3）────────────────────────────
# 消费方（必须同源，禁止各自维护副本）：
# - 本模块 should_sandbox / is_high_risk_command（沙箱路由兜底）
# - apps/tools/langchain/shell.py _assess_command_risk_level（审批风险评级，
#   经 agent_hub ApprovalPolicy.assess_risk 委托）
# 语义：全部为小写子串匹配（command.lower() 后 any(pattern in cmd)）。
# 2026-10-05 扩展（deep-audit §8.1/补充审计）：合并原 shell 侧 5 个宽泛关键字
# （install/remove/delete/format/write），并新增解释器/计划任务/证书工具等
# 逃逸面模式。SANDBOX_ENABLED=true 时这些命令强制进容器（fail-closed）；
# 沙箱关闭时经审批放行后才会落回本机。
HIGH_RISK_COMMAND_PATTERNS: tuple[str, ...] = (
    # ── 文件系统破坏 ──
    "rm -rf",
    "rm -r ",
    "rmdir",
    "del /f",
    "del /s",
    "format",
    # ── 系统/磁盘 ──
    "shutdown",
    "mkfs",
    "dd if=",
    "> /dev/sd",
    "chmod -R 777",
    "reg delete",
    "reg add",
    "vssadmin delete shadows",
    "wevtutil cl",
    # ── 远程代码执行/下载执行 ──
    # 管道执行：任何来源（curl/wget/cat/...）管道给 sh/bash。原表曾用
    # "curl | sh" 等字面模式，要求 curl 与管道符相邻，真实命令
    # "curl https://x | sh" 永远无法命中（潜伏缺陷，B3 修复）。
    "| sh",
    "| bash",
    "|sh",
    "|bash",
    # fork bomb：只锚定 distinctive 前缀 ":(){"，容忍变体空格
    # （原全字面模式 ":(){:|:&};:" 对 ":(){ :|:& };:" 失效）
    ":(){",
    "certutil -urlcache",
    "bitsadmin /transfer",
    "mshta",
    # ── 解释器直接执行（等价任意代码）──
    "python -c",
    "python3 -c",
    "node -e",
    "invoke-expression",
    "iex (",
    # ── 计划任务/网络工具 ──
    "schtasks ",
    "nc -",
    "ncat ",
    "netcat ",
    # ── 原 shell 侧宽泛关键字（保持既有审批行为，无降级）──
    "install",
    "remove",
    "delete",
    "write",
)

# 兼容别名（历史引用；新代码一律使用公开名）
_HIGH_RISK_PATTERNS = HIGH_RISK_COMMAND_PATTERNS


def is_high_risk_command(command: str) -> bool:
    """HIGH 级命令检测（权威实现，沙箱路由兜底 + 审批评级同源）。

    即使 policy 判定为 CONTROLLED，如果命令包含高危模式，
    沙箱路由仍会将其路由到容器内执行（fail-closed）。

    Args:
        command: 待检测的命令

    Returns:
        True 表示命令匹配 HIGH 级模式
    """
    cmd_lower = command.lower().strip()
    # 模式同样归一小写（修复既有潜在缺陷：原表 "chmod -R 777" 含大写 R，
    # 在小写化命令上永远无法命中）
    return any(pattern.lower() in cmd_lower for pattern in HIGH_RISK_COMMAND_PATTERNS)


# 兼容别名（历史引用；新代码一律使用公开名）
_is_high_risk_command = is_high_risk_command


# 模块级单例
sandbox_executor = SandboxExecutor()


def cleanup_sandbox(thread_id: str) -> None:
    """模块级便捷函数：清理指定 thread_id 的沙箱容器（供 research_runner 调用）。"""
    sandbox_executor.cleanup_sandbox(thread_id)
