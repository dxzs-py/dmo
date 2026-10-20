"""
通用文件管理服务
提供统一的文件列表、下载、搜索功能
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


def is_safe_relative_filename(filename: str | None) -> bool:
    """REST 视图层 filename 路径参数的入口净化（F-02 纵深防御）。

    拒绝：空值、绝对路径（根锚定/盘符/UNC）、含 ``..`` 路径段的输入。
    service 层 :meth:`FileManagerService.get_file_info` 另有 resolve +
    ``is_relative_to`` 的文件系统真实边界校验；本函数仅做视图层兜底，
    与 S-03 的 ``check_path_allowed``（agent 工具 DATA_DIR 语义）分层。
    """
    if not filename or not filename.strip():
        return False
    candidate = Path(filename)
    if candidate.is_absolute() or candidate.drive or candidate.root:
        # 绝对路径 / 盘符形态（含 "D:file"）/ 根锚定（Windows 下 "/x" 非 absolute 但有 root）
        return False
    return ".." not in candidate.parts


class FileType:
    REPORT = "report"
    NOTE = "note"
    PLAN = "plan"
    DOCUMENT = "document"
    OTHER = "other"


class FileInfo:
    def __init__(
        self,
        path: Path,
        base_dir: Path,
        file_type: str = FileType.OTHER,
        task_id: str | None = None,
    ):
        self.path = path
        self.base_dir = base_dir
        self.file_type = file_type
        self.task_id = task_id

    def to_dict(self) -> dict[str, Any]:
        stat = self.path.stat()
        return {
            "name": self.path.name,
            "relative_path": str(self.path.relative_to(self.base_dir)),
            "size": stat.st_size,
            "size_formatted": self._format_size(stat.st_size),
            "created_at": datetime.fromtimestamp(stat.st_ctime, tz=UTC).isoformat(),
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
            "file_type": self.file_type,
            "task_id": self.task_id,
            "extension": self.path.suffix.lower(),
        }

    @staticmethod
    def _format_size(size: int) -> str:
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} TB"


class FileManagerService:
    """文件管理服务"""

    def __init__(self, base_dir: Path | None = None):
        if base_dir is None:
            base_dir = Path(settings.DATA_DIR)
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def list_task_files(
        self,
        task_id: str,
        task_type: str = "research",
        subdirectory: str | None = None,
    ) -> list[FileInfo]:
        """
        列出指定任务的所有文件

        Args:
            task_id: 任务ID
            task_type: 任务类型 (research/workflow)
            subdirectory: 可选的子目录筛选

        Returns:
            文件信息列表
        """
        task_dir = self._get_task_dir(task_id, task_type)
        if not task_dir.exists():
            return []
        task_root = task_dir.resolve()

        files = []
        if subdirectory:
            search_dir = self._resolve_within_boundary(task_dir / subdirectory, task_root)
            if search_dir is None:
                logger.warning(
                    "[FileManager] 子目录越界，已拒绝列出: task=%s/%s, subdirectory=%s",
                    task_type,
                    task_id,
                    subdirectory,
                )
                return []
        else:
            search_dir = task_dir

        if not search_dir.exists():
            return []

        for file_path in search_dir.rglob("*"):
            if not file_path.is_file():
                continue
            resolved = self._resolve_within_boundary(file_path, task_root)
            if resolved is None:
                continue
            if "sandbox" in resolved.relative_to(task_root).parts:
                continue
            file_type = self._detect_file_type(file_path)
            files.append(
                FileInfo(
                    path=file_path,
                    base_dir=self.base_dir,
                    file_type=file_type,
                    task_id=task_id,
                )
            )

        return sorted(files, key=lambda f: f.path.stat().st_mtime, reverse=True)

    def get_file_info(self, task_id: str, relative_path: str, task_type: str = "research") -> FileInfo | None:
        """获取文件信息（F-02：resolve + containment 强校验，防路径穿越）。

        命名边界说明：与 S-03 的 ``check_path_allowed``（agent 工具的 DATA_DIR
        边界语义）同构但语义不同——此处约束对象是任务会话子目录（task_dir），
        故在 file_manager 内实现同构校验而非复用 DATA_DIR 准入函数，避免
        语义混用。resolve() 会展开符号链接、归一 ``..``、统一盘符大小写，
        可拦截词法 parts 比较放行的穿越与绝对路径替换。
        """
        task_dir = self._get_task_dir(task_id, task_type)
        task_root = task_dir.resolve()

        file_path = task_dir / relative_path
        resolved = self._resolve_within_boundary(file_path, task_root)
        if resolved is not None and resolved.is_file():
            file_path = resolved
        elif "/" not in relative_path and "\\" not in relative_path:
            # 纯文件名兜底：在任务目录内按名匹配（候选来自 rglob，逐一 containment 复核）
            for file_candidate in task_dir.rglob("*"):
                if not file_candidate.is_file() or file_candidate.name != relative_path:
                    continue
                candidate_resolved = self._resolve_within_boundary(file_candidate, task_root)
                if candidate_resolved is None:
                    continue
                if "sandbox" in candidate_resolved.relative_to(task_root).parts:
                    continue
                file_path = candidate_resolved
                break
            else:
                return None
        else:
            return None

        if "sandbox" in file_path.relative_to(task_root).parts:
            return None

        file_type = self._detect_file_type(file_path)
        return FileInfo(
            path=file_path,
            base_dir=self.base_dir,
            file_type=file_type,
            task_id=task_id,
        )

    def read_file_content(self, task_id: str, relative_path: str, task_type: str = "research") -> str | None:
        """读取文件内容"""
        file_info = self.get_file_info(task_id, relative_path, task_type)
        if not file_info:
            return None

        try:
            with open(file_info.path, encoding="utf-8") as f:
                return f.read()
        except Exception:
            logger.exception("读取文件失败")
            return None

    def write_file_content(self, task_id: str, relative_path: str, content: str, task_type: str = "research") -> bool:
        """写入文件内容（F-02：与 get_file_info 同一 containment 约束，防越界写）"""
        try:
            task_dir = self._get_task_dir(task_id, task_type)
            task_root = task_dir.resolve()
            file_path = self._resolve_within_boundary(task_dir / relative_path, task_root)
            if file_path is None:
                logger.warning(
                    "[FileManager] 写入路径越界，已拒绝: task=%s/%s, relative_path=%s",
                    task_type,
                    task_id,
                    relative_path,
                )
                return False
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except Exception:
            logger.exception("写入文件失败")
            return False

    def search_files(
        self,
        keyword: str,
        task_id: str | None = None,
        task_type: str | None = None,
        file_types: list[str] | None = None,
        task_ids: set | None = None,
    ) -> list[FileInfo]:
        """
        搜索文件

        Args:
            keyword: 搜索关键词
            task_id: 可选的任务ID筛选
            task_type: 可选的任务类型筛选
            file_types: 可选的文件类型筛选
            task_ids: 可选的任务ID集合，用于隔离搜索范围。
                调用方负责解析 user_id → task_ids（Task 15.4：
                移除了 core→research 的直接依赖，由调用方注入）

        Returns:
            匹配的文件列表
        """
        user_task_ids = task_ids

        results = []
        search_dirs = []

        if task_id and task_type:
            search_dirs.append(self._get_task_dir(task_id, task_type))
        elif task_type:
            type_dir = self.base_dir / task_type
            if type_dir.exists():
                search_dirs.extend([d for d in type_dir.iterdir() if d.is_dir()])
        else:
            for type_dir in ["research", "workflow"]:
                dir_path = self.base_dir / type_dir
                if dir_path.exists():
                    search_dirs.extend([d for d in dir_path.iterdir() if d.is_dir()])

        if user_task_ids is not None:
            search_dirs = [d for d in search_dirs if d.name in user_task_ids]

        keyword_lower = keyword.lower()

        for task_dir in search_dirs:
            current_task_id = task_dir.name
            task_root = task_dir.resolve()

            for file_path in task_dir.rglob("*"):
                if not file_path.is_file():
                    continue
                resolved = self._resolve_within_boundary(file_path, task_root)
                if resolved is None:
                    continue
                if "sandbox" in resolved.relative_to(task_root).parts:
                    continue

                file_type = self._detect_file_type(file_path)
                if file_types and file_type not in file_types:
                    continue

                if keyword_lower in file_path.name.lower():
                    results.append(
                        FileInfo(
                            path=file_path,
                            base_dir=self.base_dir,
                            file_type=file_type,
                            task_id=current_task_id,
                        )
                    )
                    continue

                try:
                    with open(file_path, encoding="utf-8") as f:
                        content = f.read()
                        if keyword_lower in content.lower():
                            results.append(
                                FileInfo(
                                    path=file_path,
                                    base_dir=self.base_dir,
                                    file_type=file_type,
                                    task_id=current_task_id,
                                )
                            )
                except Exception:
                    # 文件不可读（权限/编码/不存在）时跳过，继续搜索其他文件
                    logger.debug("搜索时跳过无法读取的文件: %s", file_path)
                    continue

        return results

    def _get_task_dir(self, task_id: str, task_type: str) -> Path:
        """获取任务目录"""
        return self.base_dir / task_type / task_id

    @staticmethod
    def _resolve_within_boundary(path: Path, boundary: Path) -> Path | None:
        """resolve 后强制路径位于 boundary 内，越界返回 None（F-02）。

        与 S-03 的 ``check_path_allowed``（agent 工具 DATA_DIR 边界语义）同构
        但边界语义不同：此处 boundary 为任务会话子目录（task_dir），故独立
        实现而不复用 DATA_DIR 准入函数。resolve() 非严格模式对不存在的路径
        同样归一 ``..``、展开符号链接并统一盘符大小写，杜绝词法放行。
        """
        try:
            resolved = path.resolve()
        except (OSError, ValueError):
            # OSError：文件系统错误；ValueError：路径含非法字符（如 null byte）
            return None
        if not resolved.is_relative_to(boundary):
            return None
        return resolved

    def delete_task_files(self, task_id: str, task_type: str) -> bool:
        """
        删除任务的所有文件

        Args:
            task_id: 任务ID
            task_type: 任务类型 (research/workflow)

        Returns:
            是否成功删除
        """
        import shutil

        task_dir = self._get_task_dir(task_id, task_type)
        if task_dir.exists():
            try:
                shutil.rmtree(task_dir)
                logger.info(f"[FileManager] 已删除任务目录: {task_dir}")
                return True
            except Exception:
                logger.exception(f"[FileManager] 删除任务目录失败: {task_dir}, 错误")
                return False
        return False

    @staticmethod
    def _detect_file_type(file_path: Path) -> str:
        """检测文件类型"""
        parent_name = file_path.parent.name.lower()
        if "report" in parent_name:
            return FileType.REPORT
        if "note" in parent_name:
            return FileType.NOTE
        if "plan" in parent_name:
            return FileType.PLAN

        suffix = file_path.suffix.lower()
        if suffix in [".md", ".txt"]:
            if "report" in file_path.name.lower():
                return FileType.REPORT
            if "plan" in file_path.name.lower():
                return FileType.PLAN
            return FileType.NOTE
        if suffix in [".pdf", ".doc", ".docx", ".ppt", ".pptx"]:
            return FileType.DOCUMENT

        return FileType.OTHER


_file_manager_instance: FileManagerService | None = None


def get_file_manager() -> FileManagerService:
    """获取文件管理器单例"""
    global _file_manager_instance  # noqa: PLW0603  # 单例缓存
    if _file_manager_instance is None:
        _file_manager_instance = FileManagerService()
    return _file_manager_instance
