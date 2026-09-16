"""Docker 沙箱执行层（Phase D）

为 HIGH 级工具调用提供隔离执行环境，防止不可逆变更影响宿主系统。

设计原则：
    1. 安全第一：HIGH 级操作在开启沙箱的任务中于容器内执行，资源受限、网络隔离
    2. 任务级开关：enable_sandbox 默认关闭，由用户在发起任务时自主选择
    3. 长驻复用：按 thread_id 命名长驻容器，命令经 docker exec 复用执行
    4. 自愈与 fail-closed：容器损坏自动重建一次，二次失败明确报错（不静默降级）
    5. 路径透明：DATA_DIR（= PROJECT_ROOT/data）挂载到容器 /workspace，
       Windows 宿主路径自动映射到容器内 Linux 路径，工具无感知

模块结构：
    - path_mapper.py: Windows ↔ Linux 路径映射（映射根 = DATA_DIR）
    - executor.py: 沙箱执行器（长驻容器 + 本机执行）
"""

from Django_xm.common.sandbox.executor import (
    SandboxExecutor,
    cleanup_sandbox,
    container_name,
    sandbox_executor,
)
from Django_xm.common.sandbox.path_mapper import map_to_container_path

__all__ = [
    "SandboxExecutor",
    "cleanup_sandbox",
    "container_name",
    "map_to_container_path",
    "sandbox_executor",
]
