"""Windows ↔ Linux 路径映射（沙箱执行层）

Docker Desktop on Windows 挂载卷时需要 Linux 风格路径。
本模块将 Windows 绝对路径映射到容器内的标准挂载点 /workspace。

映射规则（沙箱加固后，仅挂载 DATA_DIR，而非整个项目根）：
    数据根目录（DATA_DIR，即 PROJECT_ROOT/data）→ /workspace
    DATA_DIR 下的子路径 → /workspace/<relative>

例如（DATA_DIR = D:\\programming\\langchain\\langchain_xm\\backend\\Django_xm\\data）：
    D:\\programming\\langchain\\langchain_xm\\backend\\Django_xm\\data\\research\\t1
        → /workspace/research/t1

深度研究任务的工作目录为 data/research/{thread_id}，
容器内对应 /workspace/research/{thread_id}，产物经挂载写回宿主。

非 DATA_DIR 内的路径不映射（返回 None），调用方应拒绝在沙箱外执行。
"""

import logging
import platform
from pathlib import PurePath, PurePosixPath, PureWindowsPath

from django.conf import settings

logger = logging.getLogger(__name__)

# 容器内数据根目录挂载点
CONTAINER_WORKSPACE = "/workspace"


def _get_data_root() -> str:
    """获取数据根目录的字符串形式（用于路径匹配）。

    沙箱仅挂载 DATA_DIR（= PROJECT_ROOT/data），容器 /workspace 即宿主 DATA_DIR。
    """
    return str(settings.DATA_DIR).rstrip("\\/")


def map_to_container_path(windows_path: str) -> str | None:
    """将 Windows/宿主路径映射到容器内路径。

    Args:
        windows_path: 宿主路径（Windows 绝对路径或相对路径）

    Returns:
        容器内绝对路径（如 /workspace/research/t1），或 None（不在 DATA_DIR 内）
    """
    if not windows_path:
        return CONTAINER_WORKSPACE

    data_root = _get_data_root()

    # 相对路径：直接拼接
    if not PurePath(windows_path).is_absolute():
        return str(PurePosixPath(CONTAINER_WORKSPACE) / windows_path.replace("\\", "/"))

    # 绝对路径：检查是否在数据目录内
    try:
        host_path = PureWindowsPath(windows_path) if platform.system() == "Windows" else PurePath(windows_path)
        root_path = PureWindowsPath(data_root) if platform.system() == "Windows" else PurePath(data_root)

        # 检查是否是数据根目录或其子路径
        try:
            relative = host_path.relative_to(root_path)
        except ValueError:
            # 不在数据目录内，无法安全映射
            logger.warning(
                f"[SandboxPathMapper] 路径不在数据目录内，无法映射: path={windows_path}, data_root={data_root}"
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
