import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from Django_xm.apps.tools.base import AsyncToolMixin
from Django_xm.apps.tools.errors import TOOL_VERSION, StandardToolResult, ToolStatus

logger = logging.getLogger(__name__)


def _resolve_data_dir() -> str:
    """解析数据根目录（settings.DATA_DIR，回退 backend 同级 data/）。"""
    try:
        from django.conf import settings as django_settings

        return str(
            getattr(django_settings, "DATA_DIR", None)
            or os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                "data",
            )
        )
    except (ImportError, AttributeError):
        return str(
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                "data",
            )
        )


class ResearchFileSystem:
    def __init__(
        self,
        thread_id: str,
        base_path: str | None = None,
        user_id: int | None = None,
        session_id: str | None = None,
    ):
        """会话文件系统（fs_* 工具共享存储后端）。

        base_path 未显式传入时默认落点 ``{DATA_DIR}/chat/{user_id}/{session_id}``：
        - 目录标识取自系统上下文（RunnableConfig 注入的 user_id/session_id），
          而非 LLM 传入参数——LLM 可能传任意值（如任务主题），目录不可控，
          原默认落点硬编码 ``data/research`` 即由此与代理模式错位；
        - 深度研究链路不使用本工具（deep_builder 已过滤 fs_*，子代理继承
          父工具集亦无 fs_*），故无需 research 专属分支。
        """
        self.thread_id = thread_id

        if base_path is None:
            base_path = os.path.join(
                _resolve_data_dir(),
                "chat",
                str(user_id) if user_id is not None else "_",
                str(session_id) if session_id else str(thread_id),
            )

        self.base_path = Path(base_path)
        # workspace = base（= data/chat/{user}/{session}），不再嵌套 LLM 传入的 thread_id
        self.workspace_path = self.base_path
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
        # 如果路径中包含 ..，resolve 后的路径会"跳出"原始路径
        # 对于相对路径，检查 resolve 后是否还在 workspace 内
        return bool(".." in path or "%2e" in path.lower() or "%2E" in path)

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
                error_msg = f"写入文件失败: {e!s}"
                logger.exception(error_msg)
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
            error_msg = f"写入文件失败: {e!s}"
            logger.exception(error_msg)
            return error_msg

    def read_file(self, relative_path: str, subdirectory: str = "notes") -> str:
        if self._is_path_traversal(relative_path):
            return "错误：不允许使用路径遍历（.. 或编码绕过）"

        # 绝对路径直接读取
        if self._is_absolute_path(relative_path):
            file_path = Path(relative_path)
            try:
                with open(file_path, encoding="utf-8") as f:
                    content = f.read()
                logger.info(f"📖 读取文件: {file_path}")
                return content
            except FileNotFoundError:
                return f"错误：文件不存在: {relative_path}"
            except Exception as e:
                return f"读取文件失败: {e!s}"

        # 相对路径使用研究文件系统
        self._ensure_workspace()
        rel = Path(relative_path)
        if len(rel.parts) > 1:
            file_path = self.workspace_path / rel
        else:
            file_path = self.workspace_path / subdirectory / rel.name

        try:
            with open(file_path, encoding="utf-8") as f:
                content = f.read()
            logger.info(f"📖 读取文件: {file_path}")
            return content
        except FileNotFoundError:
            return f"错误：文件不存在: {Path(relative_path).name}"
        except Exception as e:
            error_msg = f"读取文件失败: {e!s}"
            logger.exception(error_msg)
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
                    mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=UTC).astimezone().strftime("%Y-%m-%d %H:%M")
                    file_list.append(f"  - {f.name} ({size} bytes, {mtime})")

            return "文件列表:\n" + "\n".join(file_list)
        except Exception as e:
            return f"列出文件失败: {e!s}"

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
            error_msg = f"删除文件失败: {e!s}"
            logger.exception(error_msg)
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
                        with open(file_path, encoding="utf-8") as f:
                            content = f.read()
                            if keyword.lower() in content.lower():
                                rel_path = file_path.relative_to(self.workspace_path)
                                matches.append(f"  - {rel_path}")
                    except Exception:
                        # 文件不可读（权限/编码/不存在）时跳过，继续搜索其他文件
                        logger.debug("搜索时跳过无法读取的文件: %s", file_path)
                        continue

            if matches:
                return f"找到 {len(matches)} 个匹配的文件:\n" + "\n".join(matches)
            else:
                return f"在 {subdirectory} 目录中没有找到包含 '{keyword}' 的文件"

        except Exception as e:
            return f"搜索文件失败: {e!s}"


_filesystem_instances: dict[str, ResearchFileSystem] = {}


def get_filesystem(
    thread_id: str,
    user_id: int | None = None,
    session_id: str | None = None,
) -> ResearchFileSystem:
    """获取（按 用户·会话 维度隔离的）文件系统实例。

    缓存 key 同时包含 user_id/session_id：同一会话内多工具调用共享实例，
    不同会话（即使 thread_id 相同）互不串目录。
    """
    key = f"{user_id if user_id is not None else '_'}:{session_id or thread_id}:{thread_id}"
    if key not in _filesystem_instances:
        _filesystem_instances[key] = ResearchFileSystem(
            thread_id,
            user_id=user_id,
            session_id=session_id,
        )
    return _filesystem_instances[key]


class FilesystemContextMixin:
    """从 LangGraph RunnableConfig 捕获会话上下文，解析文件系统目录归属。

    范式与 spawn_sub_agent 一致（覆写 invoke/ainvoke 捕获 configurable）：
    - ``configurable.thread_id``：主 agent = session_id / research task id；子代理 = subagent_xxx。
    - ``configurable.user_id`` / ``configurable.session_id``：主 agent 由 chat_service /
      research_runner 写入，子代理由 langgraph_adapter._build_configurable 逐层传递
      （替代历史 get_parent_tool_context 全局旁路）。

    目录归属（``_resolve_filesystem``）：
    1. 主 agent / 子代理 / 深研：统一从 configurable 读取 user_id/session_id；
    2. 兜底：上下文缺失时以 thread_id 建目录（避免崩溃）。
    """

    # 工具为模块级单例，运行时捕获值保存在实例上；LangGraph 工具调用
    # 前总是先 invoke/ainvoke 重新捕获，覆盖前一次残留（与 spawn 工具同模式）
    _captured_thread_id: str = ""
    _captured_user_id: Any = None
    _captured_session_id: str = ""
    _captured_chat_session_id: str = ""

    def invoke(self, input, config=None, **kwargs) -> Any:
        self._capture_configurable(config)
        return super().invoke(input, config=config, **kwargs)

    async def ainvoke(self, input, config=None, **kwargs) -> Any:
        self._capture_configurable(config)
        return await super().ainvoke(input, config=config, **kwargs)

    def _capture_configurable(self, config) -> None:
        if isinstance(config, dict):
            configurable = config.get("configurable", {})
            if isinstance(configurable, dict):
                self._captured_thread_id = str(configurable.get("thread_id", "") or "")
                self._captured_user_id = configurable.get("user_id")
                self._captured_session_id = str(configurable.get("session_id", "") or "")
                self._captured_chat_session_id = str(configurable.get("chat_session_id", "") or "")

    def _resolve_filesystem(self) -> ResearchFileSystem:
        thread_id = self._captured_thread_id or ""
        user_id = self._captured_user_id
        # session_id 缺失（历史/异常场景）回退 chat_session_id，再回退 thread_id
        session_id = self._captured_session_id or self._captured_chat_session_id or None
        return get_filesystem(thread_id, user_id=user_id, session_id=session_id)


class FsWriteFileInput(BaseModel):
    relative_path: str = Field(
        description="文件路径，支持相对路径（如'plan.md'、'notes/intro.md'）和绝对路径（如'D:\\docs\\report.md'）"
    )
    content: str = Field(description="要写入的内容")
    thread_id: str = Field(description="线程ID（必填；仅供隔离兼容，实际存储目录由系统会话上下文自动决定）")


class FsReadFileInput(BaseModel):
    relative_path: str = Field(description="文件路径，支持相对路径和绝对路径")
    thread_id: str = Field(description="线程ID（必填；仅供隔离兼容，实际存储目录由系统会话上下文自动决定）")


class FsListFilesInput(BaseModel):
    subdirectory: str = Field(default="notes", description="子目录名称（plans/notes/reports/temp）")
    thread_id: str = Field(description="线程ID（必填；仅供隔离兼容，实际存储目录由系统会话上下文自动决定）")


class FsSearchFilesInput(BaseModel):
    keyword: str = Field(description="要搜索的关键词")
    thread_id: str = Field(description="线程ID（必填；仅供隔离兼容，实际存储目录由系统会话上下文自动决定）")
    subdirectory: str = Field(default="notes", description="要搜索的子目录")


class FsWriteFileTool(FilesystemContextMixin, AsyncToolMixin, BaseTool):
    """文件写入工具

    审批由 ApprovalMiddleware 统一处理：
    - 绝对路径写入触发审批（FsWriteFileApprovalPolicy）
    - 相对路径写入无需审批
    工具层不参与审批判断。

    目录归属（spec unify-agent-research-display-architecture 后续决策）：
    相对路径默认落 ``data/chat/{user_id}/{session_id}/``（按用户·会话分组，
    上下文从 RunnableConfig 注入，忽略 LLM 传入的 thread_id 参数——
    该参数保留仅为兼容工具 schema 与审批展示，不再决定目录路径）。
    """

    name: str = "fs_write_file"
    version: str = TOOL_VERSION
    metadata: dict = Field(default_factory=lambda: {"tier": "extended", "visibility": "selectable", "category": "file"})
    description: str = (
        "写入内容到文件系统中的文件，用于保存中间结果、笔记、计划、报告等。"
        "适用场景：需要持久化存储中间结果、笔记、计划或报告，跨对话保存数据。"
        "不适用：读取文件（应使用 fs_read_file）、搜索文件内容（应使用 fs_search_files）。"
        "参数：relative_path-文件路径（支持相对路径如'plan.md'和绝对路径如'D:\\docs\\report.md'，必填），"
        "content-要写入的文件内容（必填），thread_id-线程ID（必填，系统会话上下文自动决定存储目录，传当前会话标识即可）。"
        "边界：禁止使用'..'路径穿越；相对路径自动根据路径匹配子目录（plans/notes/reports/temp）。"
    )
    args_schema: type[BaseModel] = FsWriteFileInput

    def _run(self, relative_path: str, content: str, thread_id: str) -> str:
        """写入文件

        审批由 ApprovalMiddleware 统一处理，工具层不参与审批判断。
        绝对路径写入到达此方法时已通过审批。
        """
        fs = self._resolve_filesystem()
        # 相对路径根据文件名匹配子目录（会话文件系统内）
        subdirectory = "notes"
        if "plans" in relative_path:
            subdirectory = "plans"
        elif "reports" in relative_path:
            subdirectory = "reports"
        return fs.write_file(relative_path, content, subdirectory)


class FsReadFileTool(FilesystemContextMixin, AsyncToolMixin, BaseTool):
    name: str = "fs_read_file"
    version: str = TOOL_VERSION
    metadata: dict = Field(default_factory=lambda: {"tier": "standard", "visibility": "core", "category": "file"})
    description: str = (
        "读取会话文件系统中指定文件的内容。"
        "适用场景：需要查看之前保存的文件内容、回顾笔记或计划。"
        "不适用：写入文件（应使用 fs_write_file）、浏览目录（应使用 fs_list_files）。"
        "参数：relative_path-相对路径文件名（必填），thread_id-线程ID（必填，系统会话上下文自动决定存储目录，传当前会话标识即可）。"
        "边界：禁止使用'..'路径穿越；文件不存在时返回错误提示。"
    )
    args_schema: type[BaseModel] = FsReadFileInput

    def _run(self, relative_path: str, thread_id: str) -> str:
        fs = self._resolve_filesystem()
        result = fs.read_file(relative_path)
        # 判断是否为错误结果
        is_error = result.startswith(("错误", "读取文件失败"))
        return StandardToolResult(
            content=result,
            status=ToolStatus.ERROR if is_error else ToolStatus.SUCCESS,
            source="filesystem",
            metadata={"thread_id": thread_id, "relative_path": relative_path},
        ).to_tool_message()


class FsListFilesTool(FilesystemContextMixin, AsyncToolMixin, BaseTool):
    name: str = "fs_list_files"
    version: str = TOOL_VERSION
    metadata: dict = Field(default_factory=lambda: {"tier": "extended", "visibility": "selectable", "category": "file"})
    description: str = (
        "列出会话文件系统中指定子目录下的所有文件，显示文件名、大小和修改时间。"
        "适用场景：需要浏览文件系统中的文件列表、确认文件是否已保存、查看目录结构。"
        "不适用：读取文件内容（应使用 fs_read_file）、搜索文件内容（应使用 fs_search_files）。"
        "参数：subdirectory-子目录名称（plans/notes/reports/temp，默认'notes'），"
        "thread_id-线程ID（必填，系统会话上下文自动决定存储目录，传当前会话标识即可）。"
        "边界：目录不存在时返回提示信息。"
    )
    args_schema: type[BaseModel] = FsListFilesInput

    def _run(self, thread_id: str, subdirectory: str = "notes") -> str:
        fs = self._resolve_filesystem()
        return fs.list_files(subdirectory)


class FsSearchFilesTool(FilesystemContextMixin, AsyncToolMixin, BaseTool):
    name: str = "fs_search_files"
    version: str = TOOL_VERSION
    metadata: dict = Field(default_factory=lambda: {"tier": "extended", "visibility": "selectable", "category": "file"})
    description: str = (
        "在会话文件系统中搜索包含指定关键词的文件，返回匹配文件列表。"
        "适用场景：需要在文件系统中查找包含特定内容的文件、定位相关笔记或报告。"
        "不适用：列出所有文件（应使用 fs_list_files）、读取文件内容（应使用 fs_read_file）。"
        "参数：keyword-要搜索的关键词（必填，不区分大小写），"
        "subdirectory-要搜索的子目录（默认'notes'），thread_id-线程ID（必填，系统会话上下文自动决定存储目录，传当前会话标识即可）。"
        "边界：递归搜索子目录，仅匹配文本文件内容。"
    )
    args_schema: type[BaseModel] = FsSearchFilesInput

    def _run(self, keyword: str, thread_id: str, subdirectory: str = "notes") -> str:
        fs = self._resolve_filesystem()
        return fs.search_files(keyword, subdirectory)


fs_write_file = FsWriteFileTool()
fs_read_file = FsReadFileTool()
fs_list_files = FsListFilesTool()
fs_search_files = FsSearchFilesTool()

FILESYSTEM_TOOLS = [fs_write_file, fs_read_file, fs_list_files, fs_search_files]


def get_filesystem_tools():
    return FILESYSTEM_TOOLS
