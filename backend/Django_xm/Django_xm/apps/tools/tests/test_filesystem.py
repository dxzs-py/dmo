"""fs_* 工具文件系统目录归属单元测试。

覆盖（spec unify-agent-research-display-architecture 后续决策）：
1. 默认落点按 用户·会话 分组：data/chat/{user_id}/{session_id}/；
2. 主 agent：从父工具上下文读取 user_id/session_id（configurable.thread_id == session_id）；
3. 子代理：thread_id = subagent_xxx，用 chat_session_id 反查父上下文；
4. 兜底：上下文缺失时以 thread_id 建目录（不崩溃）；
5. 绝对路径写入不受默认目录影响（直接写指定位置）。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python -m unittest Django_xm.apps.tools.tests.test_filesystem
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from Django_xm.apps.tools.langchain.agent_context import (
    clear_parent_tool_context,
    set_parent_tool_context,
)
from Django_xm.apps.tools.langchain.filesystem import (
    FsListFilesTool,
    FsReadFileTool,
    FsSearchFilesTool,
    FsWriteFileTool,
    ResearchFileSystem,
)

_MODULE = "Django_xm.apps.tools.langchain.filesystem"


def _mock_data_dir(tmp: str):
    return mock.patch(f"{_MODULE}._resolve_data_dir", return_value=tmp)


class TestResearchFileSystemLayout(unittest.TestCase):
    """默认落点：data/chat/{user_id}/{session_id}/，workspace 即 base。"""

    def test_chat_layout(self):
        with tempfile.TemporaryDirectory() as tmp, _mock_data_dir(tmp):
            fs = ResearchFileSystem("t1", user_id=7, session_id="sess-1")
            self.assertEqual(fs.base_path, Path(tmp) / "chat" / "7" / "sess-1")
            self.assertEqual(fs.workspace_path, fs.base_path)

    def test_missing_owner_falls_back_to_thread_id(self):
        with tempfile.TemporaryDirectory() as tmp, _mock_data_dir(tmp):
            fs = ResearchFileSystem("t1")
            self.assertEqual(fs.base_path, Path(tmp) / "chat" / "_" / "t1")

    def test_explicit_base_path_ignores_default_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            fs = ResearchFileSystem("t1", base_path=tmp, user_id=7, session_id="sess-1")
            self.assertEqual(fs.base_path, Path(tmp))
            self.assertEqual(fs.workspace_path, Path(tmp))


class TestResolveFilesystemContext(unittest.TestCase):
    """工具上下文解析：主 agent / 子代理 / 兜底。"""

    def tearDown(self):
        clear_parent_tool_context("sess-1")
        clear_parent_tool_context("subagent_abc")

    def test_main_agent_reads_parent_context(self):
        # chat_service 构建时 set_parent_tool_context(session_id, tools, config)
        set_parent_tool_context("sess-1", [], {"user_id": 7, "session_id": "sess-1"})
        tool = FsWriteFileTool()
        tool._captured_thread_id = "sess-1"
        with tempfile.TemporaryDirectory() as tmp, _mock_data_dir(tmp):
            fs = tool._resolve_filesystem()
            self.assertEqual(fs.base_path, Path(tmp) / "chat" / "7" / "sess-1")

    def test_subagent_resolves_via_chat_session_id(self):
        # 父上下文（主 agent 会话）
        set_parent_tool_context("sess-1", [], {"user_id": 7, "session_id": "sess-1"})
        tool = FsReadFileTool()
        tool._captured_thread_id = "subagent_abc"
        tool._captured_chat_session_id = "sess-1"
        with tempfile.TemporaryDirectory() as tmp, _mock_data_dir(tmp):
            fs = tool._resolve_filesystem()
            self.assertEqual(fs.base_path, Path(tmp) / "chat" / "7" / "sess-1")

    def test_no_context_falls_back_to_thread_id(self):
        tool = FsListFilesTool()
        tool._captured_thread_id = "orphan-session"
        with tempfile.TemporaryDirectory() as tmp, _mock_data_dir(tmp):
            fs = tool._resolve_filesystem()
            self.assertEqual(fs.base_path, Path(tmp) / "chat" / "_" / "orphan-session")

    def test_capture_configurable(self):
        tool = FsSearchFilesTool()
        tool._capture_configurable(
            {"configurable": {"thread_id": "sess-1", "chat_session_id": "sess-1"}}
        )
        self.assertEqual(tool._captured_thread_id, "sess-1")
        self.assertEqual(tool._captured_chat_session_id, "sess-1")


class TestAbsolutePathWriteNotAffected(unittest.TestCase):
    """绝对路径写入直接落指定位置，不受默认目录影响。"""

    def test_write_absolute_path(self):
        tool = FsWriteFileTool()
        tool._captured_thread_id = "sess-1"
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "outside.md"
            result = tool._run(str(target), "hello", "sess-1")
            self.assertIn("成功", result)
            self.assertEqual(target.read_text(encoding="utf-8"), "hello")


if __name__ == "__main__":
    unittest.main()
