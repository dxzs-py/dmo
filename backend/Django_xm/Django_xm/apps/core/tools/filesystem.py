"""
文件系统工具模块

为 DeepAgent 提供虚拟文件系统功能，用于：
1. 存储研究计划和中间结果
2. 管理研究档案
3. 控制上下文窗口大小
"""

import os
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from langchain_core.tools import tool

import logging

logger = logging.getLogger(__name__)


def get_data_dir() -> str:
    try:
        from Django_xm.apps.core.config import settings
        return getattr(settings, 'data_dir', "/tmp/data")
    except ImportError:
        import os
        return os.environ.get("DATA_DIR", "/tmp/data")


class ResearchFileSystem:
    def __init__(
        self,
        thread_id: str,
        base_path: Optional[str] = None,
    ):
        self.thread_id = thread_id

        if base_path is None:
            base_path = os.path.join(get_data_dir(), "research")

        self.base_path = Path(base_path)
        self.workspace_path = self.base_path / thread_id
        self._init_workspace()

        logger.info(f"📁 初始化研究文件系统: {self.workspace_path}")

    def _init_workspace(self) -> None:
        directories = [
            self.workspace_path,
            self.workspace_path / "plans",
            self.workspace_path / "notes",
            self.workspace_path / "reports",
            self.workspace_path / "temp",
        ]

        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

    def write_file(self, relative_path: str, content: str, subdir: str = "notes") -> str:
        if ".." in relative_path:
            return "错误：不允许使用 .. 路径"

        file_dir = self.workspace_path / subdir
        file_dir.mkdir(parents=True, exist_ok=True)

        file_path = file_dir / relative_path

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

            logger.info(f"✅ 写入文件: {file_path}")
            return f"成功写入文件: {relative_path}"
        except Exception as e:
            error_msg = f"写入文件失败: {str(e)}"
            logger.error(error_msg)
            return error_msg

    def read_file(self, relative_path: str, subdir: str = "notes") -> str:
        if ".." in relative_path:
            return "错误：不允许使用 .. 路径"

        file_path = self.workspace_path / subdir / relative_path

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            logger.info(f"📖 读取文件: {file_path}")
            return content
        except FileNotFoundError:
            return f"错误：文件不存在: {relative_path}"
        except Exception as e:
            error_msg = f"读取文件失败: {str(e)}"
            logger.error(error_msg)
            return error_msg

    def list_files(self, subdir: str = "notes", pattern: str = "*") -> str:
        search_dir = self.workspace_path / subdir

        if not search_dir.exists():
            return "目录不存在或为空"

        try:
            files = list(search_dir.glob(pattern))

            if not files:
                return f"目录 {subdir} 中没有找到匹配 {pattern} 的文件"

            file_list = []
            for f in files:
                if f.is_file():
                    size = f.stat().st_size
                    mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                    file_list.append(f"  - {f.name} ({size} bytes, {mtime})")

            return "文件列表:\n" + "\n".join(file_list)
        except Exception as e:
            return f"列出文件失败: {str(e)}"

    def delete_file(self, relative_path: str, subdir: str = "notes") -> str:
        if ".." in relative_path:
            return "错误：不允许使用 .. 路径"

        file_path = self.workspace_path / subdir / relative_path

        try:
            if file_path.exists():
                file_path.unlink()
                logger.info(f"🗑️ 删除文件: {file_path}")
                return f"成功删除文件: {relative_path}"
            else:
                return f"文件不存在: {relative_path}"
        except Exception as e:
            error_msg = f"删除文件失败: {str(e)}"
            logger.error(error_msg)
            return error_msg

    def search_files(self, keyword: str, subdir: str = "notes") -> str:
        search_dir = self.workspace_path / subdir

        if not search_dir.exists():
            return "目录不存在"

        matches = []

        try:
            for file_path in search_dir.rglob("*"):
                if file_path.is_file():
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            content = f.read()
                            if keyword.lower() in content.lower():
                                rel_path = file_path.relative_to(self.workspace_path)
                                matches.append(f"  - {rel_path}")
                    except Exception:
                        continue

            if matches:
                return f"找到 {len(matches)} 个匹配的文件:\n" + "\n".join(matches)
            else:
                return f"在 {subdir} 目录中没有找到包含 '{keyword}' 的文件"

        except Exception as e:
            return f"搜索文件失败: {str(e)}"


_filesystem_instances: Dict[str, ResearchFileSystem] = {}


def get_filesystem(thread_id: str) -> ResearchFileSystem:
    if thread_id not in _filesystem_instances:
        _filesystem_instances[thread_id] = ResearchFileSystem(thread_id)
    return _filesystem_instances[thread_id]


@tool
def fs_write_file(relative_path: str, content: str, thread_id: str = "default") -> str:
    """
    写入内容到文件

    用于保存研究笔记、计划、报告等到文件系统。

    Args:
        relative_path: 相对路径文件名，如 "plan.md"、"notes/intro.md"
        content: 要写入的内容
        thread_id: 线程 ID，用于隔离不同研究任务的文件

    Returns:
        操作结果

    Example:
        >>> fs_write_file("plan.md", "# 研究计划\\n这是我的研究计划...", "research_001")
        '成功写入文件: plan.md'
    """
    fs = get_filesystem(thread_id)
    subdir = "notes"
    if "plans" in relative_path:
        subdir = "plans"
    elif "reports" in relative_path:
        subdir = "reports"
    return fs.write_file(relative_path, content, subdir)


@tool
def fs_read_file(relative_path: str, thread_id: str = "default") -> str:
    """
    读取文件内容

    用于读取之前保存的研究笔记、计划等文件。

    Args:
        relative_path: 相对路径文件名
        thread_id: 线程 ID

    Returns:
        文件内容

    Example:
        >>> fs_read_file("plan.md", "research_001")
        '# 研究计划\\n这是我的研究计划...'
    """
    fs = get_filesystem(thread_id)
    return fs.read_file(relative_path)


@tool
def fs_list_files(subdir: str = "notes", thread_id: str = "default") -> str:
    """
    列出目录下的文件

    Args:
        subdir: 子目录名称（plans/notes/reports/temp）
        thread_id: 线程 ID

    Returns:
        文件列表
    """
    fs = get_filesystem(thread_id)
    return fs.list_files(subdir)


@tool
def fs_search_files(keyword: str, subdir: str = "notes", thread_id: str = "default") -> str:
    """
    在文件中搜索关键词

    Args:
        keyword: 要搜索的关键词
        subdir: 要搜索的子目录
        thread_id: 线程 ID

    Returns:
        搜索结果
    """
    fs = get_filesystem(thread_id)
    return fs.search_files(keyword, subdir)


FILESYSTEM_TOOLS = [fs_write_file, fs_read_file, fs_list_files, fs_search_files]


def get_filesystem_tools():
    return FILESYSTEM_TOOLS