"""Windows ↔ Linux 路径映射（沙箱执行层）

Docker Desktop on Windows 挂载卷时需要 Linux 风格路径。
本模块将 Windows 绝对路径映射到容器内的标准挂载点 /workspace。

映射规则：
    项目根目录（PROJECT_ROOT）→ /workspace
    项目根目录下的子路径 → /workspace/<relative>

例如（PROJECT_ROOT = D:\\programming\\langchain\\langchain_xm）：
    D:\\programming\\langchain\\langchain_xm\\backend → /workspace/backend
    D:\\programming\\langchain\\langchain_xm\\data\\research → /workspace/data/research

非项目目录路径不映射（返回 None），调用方应拒绝在沙箱外执行。
"""

import logging
import platform
from pathlib import PurePath, PurePosixPath, PureWindowsPath

from django.conf import settings

logger = logging.getLogger(__name__)

# 容器内项目根目录挂载点
CONTAINER_WORKSPACE = "/workspace"


def _get_project_root() -> str:
    """获取项目根目录的字符串形式（用于路径匹配）。"""
    return str(settings.PROJECT_ROOT).rstrip("\\/")


def map_to_container_path(windows_path: str) -> str | None:
    """将 Windows/宿主路径映射到容器内路径。

    Args:
        windows_path: 宿主路径（Windows 绝对路径或相对路径）

    Returns:
        容器内绝对路径（如 /workspace/backend），或 None（不在项目目录内）
    """
    if not windows_path:
        return CONTAINER_WORKSPACE

    project_root = _get_project_root()

    # 相对路径：直接拼接
    if not PurePath(windows_path).is_absolute():
        return str(PurePosixPath(CONTAINER_WORKSPACE) / windows_path.replace("\\", "/"))

    # 绝对路径：检查是否在项目目录内
    try:
        host_path = PureWindowsPath(windows_path) if platform.system() == "Windows" else PurePath(windows_path)
        root_path = PureWindowsPath(project_root) if platform.system() == "Windows" else PurePath(project_root)

        # 检查是否是项目根目录或其子路径
        try:
            relative = host_path.relative_to(root_path)
        except ValueError:
            # 不在项目目录内，无法安全映射
            logger.warning(
                f"[SandboxPathMapper] 路径不在项目目录内，无法映射: path={windows_path}, project_root={project_root}"
            )
            return None

        # 拼接容器路径
        parts = [p for p in relative.parts if p]
        if not parts:
            return CONTAINER_WORKSPACE
        return str(PurePosixPath(CONTAINER_WORKSPACE, *parts))
    except Exception as e:
        logger.warning(f"[SandboxPathMapper] 路径映射失败: path={windows_path}, error={e}")
        return None


def map_to_host_path(container_path: str) -> str | None:
    """将容器内路径映射回宿主路径（供结果路径回显）。

    Args:
        container_path: 容器内绝对路径（如 /workspace/backend/results）

    Returns:
        宿主路径字符串，或 None（不在挂载点内）
    """
    if not container_path:
        return None

    project_root = _get_project_root()

    # 检查是否以挂载点开头
    if not container_path.startswith(CONTAINER_WORKSPACE):
        return container_path  # 非挂载点路径，原样返回

    # 提取相对路径
    relative = container_path[len(CONTAINER_WORKSPACE) :].lstrip("/")
    if not relative:
        return project_root

    # 拼接宿主路径
    return str(PurePath(project_root) / relative)
