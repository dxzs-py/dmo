import os
import json
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field
import logging

from Django_xm.apps.tools.errors import StandardToolResult, ToolStatus, TOOL_VERSION
from Django_xm.apps.tools.base import AsyncToolMixin, interrupt_for_approval, reject_sync_approval

logger = logging.getLogger(__name__)


def get_data_dir() -> str:
    try:
        from django.conf import settings as django_settings
        return str(getattr(django_settings, 'TOOLS_LANGCHAIN_DIR', django_settings.DATA_DIR / 'tools' / 'langchain'))
    except (ImportError, AttributeError):
        try:
            from Django_xm.apps.ai_engine.config import settings
            return str(getattr(settings, 'TOOLS_LANGCHAIN_DIR', getattr(settings, 'DATA_DIR', settings.DATA_DIR) / 'tools' / 'langchain'))
        except (ImportError, AttributeError):
            import os
            from pathlib import Path
            base_dir = Path(__file__).resolve().parent.parent.parent.parent.parent
            data_dir = base_dir / "data" / "tools" / "langchain"
            data_dir.mkdir(parents=True, exist_ok=True)
            return str(data_dir)


class ResearchFileSystem:
    def __init__(
        self,
        thread_id: str,
        base_path: Optional[str] = None,
    ):
        self.thread_id = thread_id

        if base_path is None:
            from django.conf import settings as django_settings
            data_dir = str(getattr(django_settings, 'DATA_DIR', None) or os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                "data"
            ))
            base_path = os.path.join(data_dir, "research")

        self.base_path = Path(base_path)
        self.workspace_path = self.base_path / thread_id
        self._workspace_initialized = False

    def _ensure_workspace(self) -> None:
        """延迟初始化工作区，只有实际使用相对路径时才创建"""
        if self._workspace_initialized:
            return
        directories = [
            self.workspace_path,
            self.workspace_path / "plans",
            self.workspace_path / "notes",
            self.workspace_path / "reports",
            self.workspace_path / "temp",
        ]
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
        self._workspace_initialized = True
        logger.info(f"📁 初始化研究文件系统: {self.workspace_path}")

    def _is_absolute_path(self, path: str) -> bool:
        """判断是否为绝对路径"""
        return os.path.isabs(path) or Path(path).is_absolute()

    def _is_path_traversal(self, path: str) -> bool:
        """检查路径是否包含遍历攻击（如 ../ 或 URL 编码的 %2e%2e）"""
        # 检查 .. 组件
        resolved = Path(path).resolve()
        # 如果路径中包含 ..，resolve 后的路径会"跳出"原始路径
        # 对于相对路径，检查 resolve 后是否还在 workspace 内
        if ".." in path or "%2e" in path.lower() or "%2E" in path:
            return True
        return False

    def write_file(self, relative_path: str, content: str, subdirectory: str = "notes", **kwargs) -> str:
        if self._is_path_traversal(relative_path):
            return "错误：不允许使用路径遍历（.. 或编码绕过）"

        # 绝对路径直接写入，不经过研究文件系统
        if self._is_absolute_path(relative_path):
            file_path = Path(relative_path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                logger.info(f"✅ 写入文件: {file_path}")
                return f"成功写入文件: {relative_path}"
            except Exception as e:
                error_msg = f"写入文件失败: {str(e)}"
                logger.error(error_msg)
                return error_msg

        # 相对路径使用研究文件系统
        self._ensure_workspace()
        rel = Path(relative_path)
        if len(rel.parts) > 1:
            file_path = self.workspace_path / rel
        else:
            file_path = self.workspace_path / subdirectory / rel.name

        file_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

            logger.info(f"✅ 写入文件: {file_path}")
            return f"成功写入文件: {relative_path}"
        except Exception as e:
            error_msg = f"写入文件失败: {str(e)}"
            logger.error(error_msg)
            return error_msg

    def read_file(self, relative_path: str, subdirectory: str = "notes") -> str:
        if self._is_path_traversal(relative_path):
            return "错误：不允许使用路径遍历（.. 或编码绕过）"

        # 绝对路径直接读取
        if self._is_absolute_path(relative_path):
            file_path = Path(relative_path)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                logger.info(f"📖 读取文件: {file_path}")
                return content
            except FileNotFoundError:
                return f"错误：文件不存在: {relative_path}"
            except Exception as e:
                return f"读取文件失败: {str(e)}"

        # 相对路径使用研究文件系统
        self._ensure_workspace()
        rel = Path(relative_path)
        if len(rel.parts) > 1:
            file_path = self.workspace_path / rel
        else:
            file_path = self.workspace_path / subdirectory / rel.name

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            logger.info(f"📖 读取文件: {file_path}")
            return content
        except FileNotFoundError:
            return f"错误：文件不存在: {Path(relative_path).name}"
        except Exception as e:
            error_msg = f"读取文件失败: {str(e)}"
            logger.error(error_msg)
            return error_msg

    def list_files(self, subdirectory: str = "notes", pattern: str = "*") -> str:
        search_dir = self.workspace_path / subdirectory

        if not search_dir.exists():
            return "目录不存在或为空"

        try:
            files = list(search_dir.glob(pattern))

            if not files:
                return f"目录 {subdirectory} 中没有找到匹配 {pattern} 的文件"

            file_list = []
            for f in files:
                if f.is_file():
                    size = f.stat().st_size
                    mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                    file_list.append(f"  - {f.name} ({size} bytes, {mtime})")

            return "文件列表:\n" + "\n".join(file_list)
        except Exception as e:
            return f"列出文件失败: {str(e)}"

    def delete_file(self, relative_path: str, subdirectory: str = "notes") -> str:
        if ".." in relative_path:
            return "错误：不允许使用 .. 路径"

        rel = Path(relative_path)
        if len(rel.parts) > 1:
            file_path = self.workspace_path / rel
        else:
            file_path = self.workspace_path / subdirectory / rel.name

        try:
            if file_path.exists():
                file_path.unlink()
                logger.info(f"🗑️ 删除文件: {file_path}")
                return f"成功删除文件: {Path(relative_path).name}"
            else:
                return f"文件不存在: {Path(relative_path).name}"
        except Exception as e:
            error_msg = f"删除文件失败: {str(e)}"
            logger.error(error_msg)
            return error_msg

    def search_files(self, keyword: str, subdirectory: str = "notes") -> str:
        search_dir = self.workspace_path / subdirectory

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
                return f"在 {subdirectory} 目录中没有找到包含 '{keyword}' 的文件"

        except Exception as e:
            return f"搜索文件失败: {str(e)}"


_filesystem_instances: Dict[str, ResearchFileSystem] = {}


def get_filesystem(thread_id: str) -> ResearchFileSystem:
    if thread_id not in _filesystem_instances:
        _filesystem_instances[thread_id] = ResearchFileSystem(thread_id)
    return _filesystem_instances[thread_id]


class FsWriteFileInput(BaseModel):
    relative_path: str = Field(description="文件路径，支持相对路径（如'plan.md'、'notes/intro.md'）和绝对路径（如'D:\\docs\\report.md'）")
    content: str = Field(description="要写入的内容")
    thread_id: str = Field(description="线程ID，用于隔离不同研究任务的文件（必填）")


class FsReadFileInput(BaseModel):
    relative_path: str = Field(description="文件路径，支持相对路径和绝对路径")
    thread_id: str = Field(description="线程ID（必填）")


class FsListFilesInput(BaseModel):
    subdirectory: str = Field(default="notes", description="子目录名称（plans/notes/reports/temp）")
    thread_id: str = Field(description="线程ID（必填）")


class FsSearchFilesInput(BaseModel):
    keyword: str = Field(description="要搜索的关键词")
    thread_id: str = Field(description="线程ID（必填）")
    subdirectory: str = Field(default="notes", description="要搜索的子目录")


class FsWriteFileTool(AsyncToolMixin, BaseTool):
    name: str = "fs_write_file"
    version: str = TOOL_VERSION
    metadata: dict = {"tier": "extended", "visibility": "selectable", "category": "file"}
    description: str = (
        "写入内容到文件系统中的文件，用于保存研究笔记、计划、报告等。"
        "适用场景：需要持久化存储中间结果、研究笔记、计划或报告，跨对话保存数据。"
        "不适用：读取文件（应使用 fs_read_file）、搜索文件内容（应使用 fs_search_files）。"
        "参数：relative_path-文件路径（支持相对路径如'plan.md'和绝对路径如'D:\\docs\\report.md'，必填），"
        "content-要写入的文件内容（必填），thread_id-线程ID（用于隔离不同研究任务，必填）。"
        "边界：禁止使用'..'路径穿越；相对路径自动根据路径匹配子目录（plans/notes/reports/temp）。"
    )
    args_schema: type[BaseModel] = FsWriteFileInput

    def _run(self, relative_path: str, content: str, thread_id: str) -> str:
        """同步执行入口（绝对路径写入需要审批，同步模式下拒绝）"""
        fs = get_filesystem(thread_id)
        if os.path.isabs(relative_path):
            logger.warning(f"fs_write_file: 绝对路径写入在同步模式下无法请求审批: {relative_path}")
            return reject_sync_approval("fs_write_file", relative_path)
        subdirectory = "notes"
        if "plans" in relative_path:
            subdirectory = "plans"
        elif "reports" in relative_path:
            subdirectory = "reports"
        return fs.write_file(relative_path, content, subdirectory)

    async def _arun(self, relative_path: str, content: str, thread_id: str, **kwargs) -> str:
        """异步执行入口：在异步上下文中调用 interrupt()，确保 LangGraph 上下文正确传播"""
        fs = get_filesystem(thread_id)
        # 绝对路径写入需要用户确认（可能覆盖系统文件）
        if os.path.isabs(relative_path):
            approval = interrupt_for_approval(
                tool_name="fs_write_file",
                title="确认写入文件",
                description=f"Agent 请求写入绝对路径文件，可能覆盖已有文件。文件: {relative_path}，内容长度: {len(content)} 字符",
                operation=relative_path,
                danger_level="high",
                extra={"relative_path": relative_path, "content_length": len(content)},
            )
            if approval is True:
                return await asyncio.to_thread(fs.write_file, relative_path, content)
            else:
                return f"用户已拒绝写入文件: {relative_path}"
        # 相对路径根据文件名匹配子目录（研究文件系统内，风险较低）
        subdirectory = "notes"
        if "plans" in relative_path:
            subdirectory = "plans"
        elif "reports" in relative_path:
            subdirectory = "reports"
        return await asyncio.to_thread(fs.write_file, relative_path, content, subdirectory)


class FsReadFileTool(AsyncToolMixin, BaseTool):
    name: str = "fs_read_file"
    version: str = TOOL_VERSION
    metadata: dict = {"tier": "standard", "visibility": "core", "category": "file"}
    description: str = (
        "读取研究文件系统中指定文件的内容。"
        "适用场景：需要查看之前保存的文件内容、回顾研究笔记或计划。"
        "不适用：写入文件（应使用 fs_write_file）、浏览目录（应使用 fs_list_files）。"
        "参数：relative_path-相对路径文件名（必填），thread_id-线程ID（必填）。"
        "边界：禁止使用'..'路径穿越；文件不存在时返回错误提示。"
    )
    args_schema: type[BaseModel] = FsReadFileInput

    def _run(self, relative_path: str, thread_id: str) -> str:
        fs = get_filesystem(thread_id)
        result = fs.read_file(relative_path)
        # 判断是否为错误结果
        is_error = result.startswith("错误") or result.startswith("读取文件失败")
        return StandardToolResult(
            content=result,
            status=ToolStatus.ERROR if is_error else ToolStatus.SUCCESS,
            source="filesystem",
            metadata={"thread_id": thread_id, "relative_path": relative_path},
        ).to_tool_message()


class FsListFilesTool(AsyncToolMixin, BaseTool):
    name: str = "fs_list_files"
    version: str = TOOL_VERSION
    metadata: dict = {"tier": "extended", "visibility": "selectable", "category": "file"}
    description: str = (
        "列出研究文件系统中指定子目录下的所有文件，显示文件名、大小和修改时间。"
        "适用场景：需要浏览文件系统中的文件列表、确认文件是否已保存、查看目录结构。"
        "不适用：读取文件内容（应使用 fs_read_file）、搜索文件内容（应使用 fs_search_files）。"
        "参数：subdirectory-子目录名称（plans/notes/reports/temp，默认'notes'），"
        "thread_id-线程ID（必填）。"
        "边界：目录不存在时返回提示信息。"
    )
    args_schema: type[BaseModel] = FsListFilesInput

    def _run(self, thread_id: str, subdirectory: str = "notes") -> str:
        fs = get_filesystem(thread_id)
        return fs.list_files(subdirectory)


class FsSearchFilesTool(AsyncToolMixin, BaseTool):
    name: str = "fs_search_files"
    version: str = TOOL_VERSION
    metadata: dict = {"tier": "extended", "visibility": "selectable", "category": "file"}
    description: str = (
        "在研究文件系统中搜索包含指定关键词的文件，返回匹配文件列表。"
        "适用场景：需要在文件系统中查找包含特定内容的文件、定位相关笔记或报告。"
        "不适用：列出所有文件（应使用 fs_list_files）、读取文件内容（应使用 fs_read_file）。"
        "参数：keyword-要搜索的关键词（必填，不区分大小写），"
        "subdirectory-要搜索的子目录（默认'notes'），thread_id-线程ID（必填）。"
        "边界：递归搜索子目录，仅匹配文本文件内容。"
    )
    args_schema: type[BaseModel] = FsSearchFilesInput

    def _run(self, keyword: str, thread_id: str, subdirectory: str = "notes") -> str:
        fs = get_filesystem(thread_id)
        return fs.search_files(keyword, subdirectory)


fs_write_file = FsWriteFileTool()
fs_read_file = FsReadFileTool()
fs_list_files = FsListFilesTool()
fs_search_files = FsSearchFilesTool()

FILESYSTEM_TOOLS = [fs_write_file, fs_read_file, fs_list_files, fs_search_files]


def get_filesystem_tools():
    return FILESYSTEM_TOOLS
