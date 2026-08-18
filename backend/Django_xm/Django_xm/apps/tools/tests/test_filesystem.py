"""fs_* 工具文件系统目录归属单元测试。

覆盖（spec unify-agent-research-display-architecture 后续决策）：
1. 默认落点按 用户·会话 分组：data/chat/{user_id}/{session_id}/；
2. 主 agent / 子代理 / 深研：统一从 configurable 读取 user_id/session_id
   （主 agent 由 chat_service / research_runner 写入，子代理由
   langgraph_adapter._build_configurable 逐层传递，替代历史
   get_parent_tool_context 全局旁路）；
3. 兜底：上下文缺失时以 thread_id 建目录（不崩溃）；
4. 绝对路径写入不受默认目录影响（直接写指定位置）。

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
    """工具上下文解析：主 agent / 子代理 / 兜底（configurable 显式传递）。"""

    def test_main_agent_reads_configurable(self):
        # chat_service 构建时 configurable 写入 user_id/session_id
        tool = FsWriteFileTool()
        tool._captured_thread_id = "sess-1"
        tool._captured_user_id = 7
        tool._captured_session_id = "sess-1"
        with tempfile.TemporaryDirectory() as tmp, _mock_data_dir(tmp):
            fs = tool._resolve_filesystem()
            self.assertEqual(fs.base_path, Path(tmp) / "chat" / "7" / "sess-1")

    def test_subagent_inherits_configurable(self):
        # langgraph_adapter._build_configurable 逐层传递 user_id/session_id，
        # 子代理不再需要 chat_session_id 反查父上下文
        tool = FsReadFileTool()
        tool._captured_thread_id = "subagent_abc"
        tool._captured_user_id = 7
        tool._captured_session_id = "sess-1"
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
            {
                "configurable": {
                    "thread_id": "subagent_abc",
                    "user_id": 7,
                    "session_id": "sess-1",
                    "chat_session_id": "sess-1",
                }
            }
        )
        self.assertEqual(tool._captured_thread_id, "subagent_abc")
        self.assertEqual(tool._captured_user_id, 7)
        self.assertEqual(tool._captured_session_id, "sess-1")
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
