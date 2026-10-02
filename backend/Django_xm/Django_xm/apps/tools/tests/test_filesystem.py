"""fs_* 工具文件系统目录归属与路径安全单元测试。

覆盖（spec unify-agent-research-display-architecture 后续决策）：
1. 默认落点按 用户·会话 分组：data/chat/{user_id}/{session_id}/；
2. 主 agent / 子代理 / 深研：统一从 configurable 读取 user_id/session_id
   （主 agent 由 chat_service / research_runner 写入，子代理由
   langgraph_adapter._build_configurable 逐层传递，替代历史
   get_parent_tool_context 全局旁路）；
3. 兜底：上下文缺失时以 thread_id 建目录（不崩溃）；
4. 安全边界（S-03/S-06）：绝对路径统一收敛到 DATA_DIR 内，
   DATA_DIR 外路径读写拒绝；敏感文件清单（.env/settings.py/私钥等）命中即硬拒绝；
5. 相对路径正常读写不受收紧影响。

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
    _filesystem_instances,
)

_MODULE = "Django_xm.apps.tools.langchain.filesystem"


def _mock_data_dir(tmp: str):
    return mock.patch(f"{_MODULE}._resolve_data_dir", return_value=tmp)


class _ClearFsCacheMixin(unittest.TestCase):
    """每个测试前后清空 fs 实例缓存。

    get_filesystem 按用户·会话维度缓存 ResearchFileSystem（模块级单例），
    跨测试不清缓存会把上一个 tmp 目录的实例泄漏给后续同 key 测试。
    """

    def setUp(self):
        super().setUp()
        _filesystem_instances.clear()
        self.addCleanup(_filesystem_instances.clear)


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


class TestResolveFilesystemContext(_ClearFsCacheMixin):
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


class TestAbsolutePathWriteNotAffected(_ClearFsCacheMixin):
    """绝对路径写入受 DATA_DIR 硬边界约束（安全修复 S-03/S-06）。

    旧语义"绝对路径直接落指定位置"已收紧：仅允许 DATA_DIR 内路径。
    """

    def test_write_absolute_path_outside_data_dir_rejected(self):
        """DATA_DIR 外绝对路径写入 → 拒绝，文件不落盘。"""
        tool = FsWriteFileTool()
        tool._captured_thread_id = "sess-1"
        with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as data_dir:
            target = Path(outside) / "outside.md"
            with _mock_data_dir(data_dir):
                result = tool._run(str(target), "hello", "sess-1")
            self.assertIn("错误", result)
            self.assertIn("拒绝访问", result)
            self.assertFalse(target.exists())

    def test_write_absolute_path_inside_data_dir_ok(self):
        """DATA_DIR 内绝对路径写入 → 正常落盘（正常业务不受影响）。"""
        tool = FsWriteFileTool()
        tool._captured_thread_id = "sess-1"
        with tempfile.TemporaryDirectory() as data_dir:
            target = Path(data_dir) / "chat" / "7" / "sess-1" / "reports" / "report.md"
            with _mock_data_dir(data_dir):
                result = tool._run(str(target), "hello", "sess-1")
            self.assertIn("成功", result)
            self.assertEqual(target.read_text(encoding="utf-8"), "hello")

    def test_read_absolute_path_outside_data_dir_rejected(self):
        """DATA_DIR 外绝对路径读取（模拟读 C:\\ 外部文件）→ 拒绝。"""
        tool = FsReadFileTool()
        tool._captured_thread_id = "sess-1"
        with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as data_dir:
            secret = Path(outside) / "secret.txt"
            secret.write_text("top secret", encoding="utf-8")
            with _mock_data_dir(data_dir):
                result = tool._run(str(secret), "sess-1")
            self.assertIn("拒绝访问", result)
            self.assertNotIn("top secret", result)


class TestSensitiveFileProtection(_ClearFsCacheMixin):
    """敏感文件保护清单：命中即硬拒绝（即使路径在 DATA_DIR 内、审批通过）。"""

    _SENSITIVE_CASES = [
        ".env",
        ".env.local",
        "settings.py",
        "id_rsa",
        "server.pem",
        "server.key",
        "schema.yml",
        "credentials.json",
    ]

    def test_read_sensitive_files_rejected(self):
        for name in self._SENSITIVE_CASES:
            with self.subTest(name=name):
                tool = FsReadFileTool()
                tool._captured_thread_id = "sess-1"
                with tempfile.TemporaryDirectory() as data_dir:
                    target = Path(data_dir) / "chat" / "7" / "sess-1" / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("secret content", encoding="utf-8")
                    with _mock_data_dir(data_dir):
                        result = tool._run(str(target), "sess-1")
                    self.assertIn("敏感文件保护清单", result)
                    self.assertNotIn("secret content", result)

    def test_write_sensitive_files_rejected(self):
        for name in self._SENSITIVE_CASES:
            with self.subTest(name=name):
                tool = FsWriteFileTool()
                tool._captured_thread_id = "sess-1"
                with tempfile.TemporaryDirectory() as data_dir:
                    target = Path(data_dir) / "chat" / "7" / "sess-1" / name
                    with _mock_data_dir(data_dir):
                        result = tool._run(str(target), "evil", "sess-1")
                    self.assertIn("敏感文件保护清单", result)
                    self.assertFalse(target.exists())

    def test_settings_directory_part_rejected(self):
        """路径任一组件为 settings（Django settings 目录）→ 拒绝。"""
        tool = FsReadFileTool()
        tool._captured_thread_id = "sess-1"
        with tempfile.TemporaryDirectory() as data_dir:
            target = Path(data_dir) / "settings" / "base.py"
            with _mock_data_dir(data_dir):
                result = tool._run(str(target), "sess-1")
            self.assertIn("敏感文件保护清单", result)

    def test_normal_files_unaffected(self):
        """非敏感文件正常读写（清单不误伤普通业务文件）。"""
        write_tool = FsWriteFileTool()
        read_tool = FsReadFileTool()
        for t in (write_tool, read_tool):
            t._captured_thread_id = "sess-1"
        with tempfile.TemporaryDirectory() as data_dir:
            with _mock_data_dir(data_dir):
                write_result = write_tool._run("notes/report.md", "hello", "sess-1")
                read_result = read_tool._run("notes/report.md", "sess-1")
        self.assertIn("成功", write_result)
        self.assertEqual(read_result, "hello")


class TestRelativePathRoundTrip(_ClearFsCacheMixin):
    """相对路径正常读写不受边界收紧影响（含显式 base_path 场景）。"""

    def test_default_workspace_roundtrip(self):
        tool = FsWriteFileTool()
        tool._captured_thread_id = "sess-1"
        tool._captured_user_id = 7
        tool._captured_session_id = "sess-1"
        read_tool = FsReadFileTool()
        read_tool._captured_thread_id = "sess-1"
        read_tool._captured_user_id = 7
        read_tool._captured_session_id = "sess-1"
        with tempfile.TemporaryDirectory() as tmp, _mock_data_dir(tmp):
            self.assertIn("成功", tool._run("plan.md", "content", "sess-1"))
            self.assertEqual(read_tool._run("plan.md", "sess-1"), "content")

    def test_explicit_base_path_roundtrip(self):
        """显式 base_path（DATA_DIR 外，测试/系统集成场景）相对路径仍可用。"""
        with tempfile.TemporaryDirectory() as tmp, _mock_data_dir(tmp):
            fs = ResearchFileSystem("t1", base_path=tmp, user_id=7, session_id="sess-1")
            self.assertIn("成功", fs.write_file("a.md", "x", "notes"))
            self.assertEqual(fs.read_file("a.md", "notes"), "x")


if __name__ == "__main__":
    unittest.main()
