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
from typing import ClassVar
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


def _ctx(**configurable):
    """构造注入用 RunnableConfig（工具经 ``config: RunnableConfig`` 参数按调用读取）。"""
    return {"configurable": configurable}


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
        with tempfile.TemporaryDirectory() as tmp, _mock_data_dir(tmp):
            fs = tool._resolve_filesystem(_ctx(thread_id="sess-1", user_id=7, session_id="sess-1"))
            self.assertEqual(fs.base_path, Path(tmp) / "chat" / "7" / "sess-1")

    def test_subagent_inherits_configurable(self):
        # langgraph_adapter._build_configurable 逐层传递 user_id/session_id，
        # 子代理不再需要 chat_session_id 反查父上下文
        tool = FsReadFileTool()
        with tempfile.TemporaryDirectory() as tmp, _mock_data_dir(tmp):
            fs = tool._resolve_filesystem(_ctx(thread_id="subagent_abc", user_id=7, session_id="sess-1"))
            self.assertEqual(fs.base_path, Path(tmp) / "chat" / "7" / "sess-1")

    def test_no_context_falls_back_to_thread_id(self):
        tool = FsListFilesTool()
        with tempfile.TemporaryDirectory() as tmp, _mock_data_dir(tmp):
            fs = tool._resolve_filesystem(_ctx(thread_id="orphan-session"))
            self.assertEqual(fs.base_path, Path(tmp) / "chat" / "_" / "orphan-session")

    def test_session_id_falls_back_to_chat_session_id(self):
        """session_id 缺失时回退 chat_session_id（config 按调用读取，无实例残留）。"""
        tool = FsSearchFilesTool()
        with tempfile.TemporaryDirectory() as tmp, _mock_data_dir(tmp):
            fs = tool._resolve_filesystem(
                _ctx(thread_id="subagent_abc", user_id=7, session_id="", chat_session_id="chat-9")
            )
            self.assertEqual(fs.base_path, Path(tmp) / "chat" / "7" / "chat-9")

    def test_concurrent_calls_no_instance_state(self):
        """两次不同 config 的 _run 互不串目录（单例工具无实例字段缓存）。"""
        write_tool = FsWriteFileTool()
        with tempfile.TemporaryDirectory() as tmp, _mock_data_dir(tmp):
            result_a = write_tool._run(
                "notes/a.md", "A", "sess-a", _ctx(thread_id="sess-a", user_id=7, session_id="sess-a")
            )
            result_b = write_tool._run(
                "notes/b.md", "B", "sess-b", _ctx(thread_id="sess-b", user_id=8, session_id="sess-b")
            )
            self.assertIn("成功", result_a)
            self.assertIn("成功", result_b)
            self.assertTrue((Path(tmp) / "chat" / "7" / "sess-a" / "notes" / "a.md").exists())
            self.assertTrue((Path(tmp) / "chat" / "8" / "sess-b" / "notes" / "b.md").exists())
            self.assertFalse((Path(tmp) / "chat" / "7" / "sess-a" / "notes" / "b.md").exists())


class TestAbsolutePathWriteNotAffected(_ClearFsCacheMixin):
    """绝对路径写入受 DATA_DIR 硬边界约束（安全修复 S-03/S-06）。

    旧语义"绝对路径直接落指定位置"已收紧：仅允许 DATA_DIR 内路径。
    """

    def test_write_absolute_path_outside_data_dir_rejected(self):
        """DATA_DIR 外绝对路径写入 → 拒绝，文件不落盘。"""
        tool = FsWriteFileTool()
        with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as data_dir:
            target = Path(outside) / "outside.md"
            with _mock_data_dir(data_dir):
                result = tool._run(str(target), "hello", "sess-1", _ctx(thread_id="sess-1"))
            self.assertIn("错误", result)
            self.assertIn("拒绝访问", result)
            self.assertFalse(target.exists())

    def test_write_absolute_path_inside_data_dir_ok(self):
        """DATA_DIR 内绝对路径写入 → 正常落盘（正常业务不受影响）。"""
        tool = FsWriteFileTool()
        with tempfile.TemporaryDirectory() as data_dir:
            target = Path(data_dir) / "chat" / "7" / "sess-1" / "reports" / "report.md"
            with _mock_data_dir(data_dir):
                result = tool._run(str(target), "hello", "sess-1", _ctx(thread_id="sess-1"))
            self.assertIn("成功", result)
            self.assertEqual(target.read_text(encoding="utf-8"), "hello")

    def test_read_absolute_path_outside_data_dir_rejected(self):
        """DATA_DIR 外绝对路径读取（模拟读 C:\\ 外部文件）→ 拒绝。"""
        tool = FsReadFileTool()
        with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as data_dir:
            secret = Path(outside) / "secret.txt"
            secret.write_text("top secret", encoding="utf-8")
            with _mock_data_dir(data_dir):
                result = tool._run(str(secret), "sess-1", _ctx(thread_id="sess-1"))
            self.assertIn("拒绝访问", result)
            self.assertNotIn("top secret", result)


class TestSensitiveFileProtection(_ClearFsCacheMixin):
    """敏感文件保护清单：命中即硬拒绝（即使路径在 DATA_DIR 内、审批通过）。"""

    _SENSITIVE_CASES: ClassVar[list] = [
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
                with tempfile.TemporaryDirectory() as data_dir:
                    target = Path(data_dir) / "chat" / "7" / "sess-1" / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("secret content", encoding="utf-8")
                    with _mock_data_dir(data_dir):
                        result = tool._run(str(target), "sess-1", _ctx(thread_id="sess-1"))
                    self.assertIn("敏感文件保护清单", result)
                    self.assertNotIn("secret content", result)

    def test_write_sensitive_files_rejected(self):
        for name in self._SENSITIVE_CASES:
            with self.subTest(name=name):
                tool = FsWriteFileTool()
                with tempfile.TemporaryDirectory() as data_dir:
                    target = Path(data_dir) / "chat" / "7" / "sess-1" / name
                    with _mock_data_dir(data_dir):
                        result = tool._run(str(target), "evil", "sess-1", _ctx(thread_id="sess-1"))
                    self.assertIn("敏感文件保护清单", result)
                    self.assertFalse(target.exists())

    def test_settings_directory_part_rejected(self):
        """路径任一组件为 settings（Django settings 目录）→ 拒绝。"""
        tool = FsReadFileTool()
        with tempfile.TemporaryDirectory() as data_dir:
            target = Path(data_dir) / "settings" / "base.py"
            with _mock_data_dir(data_dir):
                result = tool._run(str(target), "sess-1", _ctx(thread_id="sess-1"))
            self.assertIn("敏感文件保护清单", result)

    def test_normal_files_unaffected(self):
        """非敏感文件正常读写（清单不误伤普通业务文件）。"""
        write_tool = FsWriteFileTool()
        read_tool = FsReadFileTool()
        with tempfile.TemporaryDirectory() as data_dir, _mock_data_dir(data_dir):
            write_result = write_tool._run("notes/report.md", "hello", "sess-1", _ctx(thread_id="sess-1"))
            read_result = read_tool._run("notes/report.md", "sess-1", _ctx(thread_id="sess-1"))
        self.assertIn("成功", write_result)
        self.assertEqual(read_result, "hello")


class TestRelativePathRoundTrip(_ClearFsCacheMixin):
    """相对路径正常读写不受边界收紧影响（含显式 base_path 场景）。"""

    def test_default_workspace_roundtrip(self):
        tool = FsWriteFileTool()
        read_tool = FsReadFileTool()
        config = _ctx(thread_id="sess-1", user_id=7, session_id="sess-1")
        with tempfile.TemporaryDirectory() as tmp, _mock_data_dir(tmp):
            self.assertIn("成功", tool._run("plan.md", "content", "sess-1", config))
            self.assertEqual(read_tool._run("plan.md", "sess-1", config), "content")

    def test_explicit_base_path_roundtrip(self):
        """显式 base_path（DATA_DIR 外，测试/系统集成场景）相对路径仍可用。"""
        with tempfile.TemporaryDirectory() as tmp, _mock_data_dir(tmp):
            fs = ResearchFileSystem("t1", base_path=tmp, user_id=7, session_id="sess-1")
            self.assertIn("成功", fs.write_file("a.md", "x", "notes"))
            self.assertEqual(fs.read_file("a.md", "notes"), "x")


class TestSubdirectoryBoundary(_ClearFsCacheMixin):
    """F2：list_files/search_files 的 subdirectory（LLM 可控 schema 参数）边界校验。

    pathlib 语义下 workspace / "C:/x"（绝对路径）会整体覆盖 workspace 前缀，
    "../" 可逃出会话工作区（search_files 还会读取文件内容）。两类入口一律拒绝；
    workspace 内子目录（含嵌套）不受影响。
    """

    def _make_fs(self, tmp: str) -> ResearchFileSystem:
        fs = ResearchFileSystem("t1", base_path=tmp, user_id=7, session_id="sess-1")
        fs.write_file("notes/a.md", "hello keyword", "notes")
        return fs

    def test_list_files_absolute_subdirectory_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            fs = self._make_fs(tmp)
            outside = str(Path(tmp).parent)
            result = fs.list_files(outside)
            self.assertIn("错误", result)
            self.assertIn("绝对路径", result)

    def test_list_files_traversal_subdirectory_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            fs = self._make_fs(tmp)
            for evil in ("..", "../..", "notes/../../.."):
                with self.subTest(evil=evil):
                    result = fs.list_files(evil)
                    self.assertIn("路径遍历", result)

    def test_search_files_absolute_subdirectory_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            fs = self._make_fs(tmp)
            result = fs.search_files("keyword", str(Path(tmp).parent))
            self.assertIn("绝对路径", result)

    def test_search_files_traversal_rejected_and_no_leak(self):
        """穿越/绝对路径 subdirectory 被拒，workspace 外文件不被搜索泄漏。"""
        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "secret.txt"
            secret.write_text("needle_kw here", encoding="utf-8")
            ws = Path(tmp) / "ws"
            fs = ResearchFileSystem("t1", base_path=str(ws), user_id=7, session_id="sess-1")
            result = fs.search_files("needle_kw", "..")
            self.assertIn("路径遍历", result)
            self.assertNotIn("secret.txt", result)
            result_abs = fs.search_files("needle_kw", str(Path(tmp).parent))
            self.assertIn("绝对路径", result_abs)
            self.assertNotIn("secret.txt", result_abs)

    def test_drive_relative_subdirectory_falls_back_to_boundary_check(self):
        """Windows 盘符相对路径（"C:evil"）不产生越界列举。

        当前 pathlib 语义下 "C:evil" 作为普通相对组件落回 workspace 内
        （list_files 返回"目录不存在"）；若 join 发生盘符重置逃逸，则由
        check_path_allowed 边界兜底拒绝。两种结果都安全，且都不得泄漏
        workspace 外内容。
        """
        if os.name != "nt":
            self.skipTest("仅 Windows 存在盘符相对路径语义")
        with tempfile.TemporaryDirectory() as tmp:
            fs = self._make_fs(tmp)
            result = fs.list_files("C:evil")
            # 拒绝文本或"目录不存在"提示均安全；关键性质：不泄漏 workspace 外内容
            self.assertNotIn("a.md", result)
            self.assertNotIn(str(Path(tmp).parent), result)

    def test_normal_and_nested_subdirectory_unaffected(self):
        """workspace 内子目录（含嵌套）列举/搜索不受收紧影响。"""
        with tempfile.TemporaryDirectory() as tmp:
            fs = ResearchFileSystem("t1", base_path=tmp, user_id=7, session_id="sess-1")
            fs.write_file("notes/sub/deep.md", "deep keyword", "notes")
            self.assertIn("deep.md", fs.list_files("notes/sub"))
            # Windows 下 relative_to 输出反斜杠，归一化后断言
            self.assertIn(
                "notes/sub/deep.md",
                fs.search_files("keyword", "notes").replace("\\", "/"),
            )


if __name__ == "__main__":
    unittest.main()
