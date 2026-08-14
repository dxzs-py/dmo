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

        files = []
        search_dir = task_dir / subdirectory if subdirectory else task_dir

        if not search_dir.exists():
            return []

        for file_path in search_dir.rglob("*"):
            if file_path.is_file():
                if "sandbox" in file_path.relative_to(task_dir).parts:
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
        """获取文件信息"""
        task_dir = self._get_task_dir(task_id, task_type)

        file_path = task_dir / relative_path
        if file_path.exists() and file_path.is_file():
            pass
        elif "/" not in relative_path and "\\" not in relative_path:
            for file_candidate in task_dir.rglob("*"):
                if file_candidate.is_file() and file_candidate.name == relative_path:
                    if "sandbox" in file_candidate.relative_to(task_dir).parts:
                        continue
                    file_path = file_candidate
                    break
            else:
                return None
        else:
            return None

        if "sandbox" in file_path.relative_to(task_dir).parts:
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
        """写入文件内容"""
        try:
            task_dir = self._get_task_dir(task_id, task_type)
            file_path = task_dir / relative_path
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

            for file_path in task_dir.rglob("*"):
                if not file_path.is_file():
                    continue
                if "sandbox" in file_path.relative_to(task_dir).parts:
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
