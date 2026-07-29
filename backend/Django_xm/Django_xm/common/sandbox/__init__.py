"""Docker 沙箱执行层（Phase D）

为 HIGH 级工具调用提供隔离执行环境，防止不可逆变更影响宿主系统。

设计原则：
    1. 安全第一：HIGH 级操作强制在容器内执行，资源受限、网络隔离
    2. 优雅降级：Docker 不可用时回退到本机执行 + 审计日志（SANDBOX_ENABLED 控制）
    3. 路径透明：Windows 路径自动映射到容器内 Linux 路径，工具无感知
    4. 单次执行：每个命令启动独立容器，避免状态泄漏（会话级复用可后续扩展）

模块结构：
    - path_mapper.py: Windows ↔ Linux 路径映射
    - executor.py: 沙箱执行器（Docker + 本机降级）
"""

from Django_xm.common.sandbox.executor import SandboxExecutor, sandbox_executor
from Django_xm.common.sandbox.path_mapper import map_to_container_path

__all__ = ["SandboxExecutor", "map_to_container_path", "sandbox_executor"]
