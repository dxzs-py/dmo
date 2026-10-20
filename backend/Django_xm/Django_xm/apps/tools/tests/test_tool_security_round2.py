"""工具安全修复（fix-review-round2 Task 2）tools 侧单元测试。

覆盖：
1. 2.3 fs_read_file 大小上限（MAX_FILE_SIZE 超限拒绝）与内容截断
   （MAX_CONTENT_LENGTH，文案对齐 file_reader）；绝对路径分支同口径；
2. 2.3 fs_search_files 内容预算：超限文件跳过匹配；
3. 2.4 web_search 异常脱敏：分类文案进模型上下文，原始异常不外泄；
4. 2.7 MCP 多 server 并行建连：一坏一好，好的正常装配且总耗时 ≈ 单 server。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.tools.tests.test_tool_security_round2 --noinput
"""

import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import SimpleTestCase

from Django_xm.apps.tools.langchain.filesystem import (
    MAX_CONTENT_LENGTH,
    MAX_FILE_SIZE,
    ResearchFileSystem,
    _filesystem_instances,
)
from Django_xm.async_utils import run_async

_FS_MODULE = "Django_xm.apps.tools.langchain.filesystem"


def _mock_data_dir(tmp: str):
    return patch(f"{_FS_MODULE}._resolve_data_dir", return_value=tmp)


class _ClearFsCacheMixin(unittest.TestCase):
    def setUp(self):
        super().setUp()
        _filesystem_instances.clear()
        self.addCleanup(_filesystem_instances.clear)


class TestReadFileSizeLimit(_ClearFsCacheMixin, SimpleTestCase):
    """2.3：fs_read_file 大小上限（相对/绝对路径分支同口径，对齐 file_reader）。"""

    def test_relative_path_oversized_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, _mock_data_dir(tmp):
            fs = ResearchFileSystem("t1", user_id=1, session_id="s1")
            fs.write_file("big.md", "x" * 100, "notes")
            target = fs.workspace_path / "notes" / "big.md"
            with patch(f"{_FS_MODULE}.MAX_FILE_SIZE", 10):
                result = fs.read_file("notes/big.md")
            self.assertTrue(result.startswith("错误：文件过大"), result[:80])
            self.assertEqual(target.stat().st_size, 100)  # 文件未被修改

    def test_relative_path_within_limit_reads_normally(self):
        with tempfile.TemporaryDirectory() as tmp, _mock_data_dir(tmp):
            fs = ResearchFileSystem("t1", user_id=1, session_id="s1")
            fs.write_file("ok.md", "hello budget", "notes")
            with patch(f"{_FS_MODULE}.MAX_FILE_SIZE", 100):
                self.assertEqual(fs.read_file("notes/ok.md"), "hello budget")

    def test_absolute_path_oversized_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, _mock_data_dir(tmp):
            fs = ResearchFileSystem("t1", user_id=1, session_id="s1")
            fs.write_file("abs.md", "y" * 100, "notes")
            abs_path = str(fs.workspace_path / "notes" / "abs.md")
            with patch(f"{_FS_MODULE}.MAX_FILE_SIZE", 10):
                result = fs.read_file(abs_path)
            self.assertTrue(result.startswith("错误：文件过大"), result[:80])

    def test_content_truncation_marker_matches_file_reader(self):
        """超 MAX_CONTENT_LENGTH 截断，截断标记与 file_reader 文案一致。"""
        with tempfile.TemporaryDirectory() as tmp, _mock_data_dir(tmp):
            fs = ResearchFileSystem("t1", user_id=1, session_id="s1")
            content = "z" * 120
            fs.write_file("long.md", content, "notes")
            with patch(f"{_FS_MODULE}.MAX_CONTENT_LENGTH", 50):
                result = fs.read_file("notes/long.md")
            self.assertTrue(result.startswith("z" * 50))
            self.assertIn("[文件内容过长，已截断，原始长度: 120 字符]", result)

    def test_constants_align_with_file_reader(self):
        """常量口径对齐 file_reader（10MB / 50000），防止漂移。"""
        import importlib

        file_reader_module = importlib.import_module("Django_xm.apps.tools.langchain.file_reader")

        self.assertEqual(MAX_FILE_SIZE, file_reader_module.MAX_FILE_SIZE)
        self.assertEqual(MAX_CONTENT_LENGTH, file_reader_module.MAX_CONTENT_LENGTH)


class TestSearchFilesContentBudget(_ClearFsCacheMixin, SimpleTestCase):
    """2.3：search_files 跳过超限文件，不因单文件超大导致全量读入。"""

    def test_oversized_file_skipped_from_matches(self):
        with tempfile.TemporaryDirectory() as tmp, _mock_data_dir(tmp):
            fs = ResearchFileSystem("t1", user_id=1, session_id="s1")
            fs.write_file("small_hit.md", "needle_kw here", "notes")
            fs.write_file("big_hit.md", "needle_kw " + "x" * 100, "notes")
            with patch(f"{_FS_MODULE}.MAX_FILE_SIZE", 50):
                result = fs.search_files("needle_kw", "notes")
            self.assertIn("small_hit.md", result)
            self.assertNotIn("big_hit.md", result)


class TestWebSearchErrorSanitization(SimpleTestCase):
    """2.4：搜索异常分类脱敏，原始异常不进模型上下文。"""

    def test_auth_or_quota_error(self):
        from Django_xm.apps.tools.langchain.web_search import _classify_search_error

        msg = _classify_search_error(Exception("401 Unauthorized: invalid api key"))
        self.assertIn("认证或配额", msg)

    def test_network_error(self):
        from Django_xm.apps.tools.langchain.web_search import _classify_search_error

        msg = _classify_search_error(TimeoutError("connection timed out"))
        self.assertIn("网络连接异常", msg)

    def test_server_unavailable_error(self):
        from Django_xm.apps.tools.langchain.web_search import _classify_search_error

        msg = _classify_search_error(Exception("503 Service Unavailable"))
        self.assertIn("暂时不可用", msg)

    def test_unknown_error_falls_back_to_generic(self):
        from Django_xm.apps.tools.langchain.web_search import _classify_search_error

        msg = _classify_search_error(ValueError("weird internal state"))
        self.assertIn("搜索失败", msg)

    def test_tool_run_leaks_no_original_exception(self):
        """工具输出为分类文案，API Key 片段等原始异常细节不得外泄。"""
        from Django_xm.apps.tools.langchain.web_search import web_search

        with patch(
            "Django_xm.apps.tools.langchain.web_search.create_tavily_search_tool",
            side_effect=Exception("401 Unauthorized: api key sk-secret-abc123 invalid"),
        ):
            result = web_search._run("test query")
        self.assertIn("认证或配额", result)
        self.assertNotIn("sk-secret-abc123", result)
        self.assertNotIn("Unauthorized", result)


def _fake_mcp_tool(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, description="demo", metadata={})


class TestMcpParallelFetch(SimpleTestCase):
    """2.7：多 server 并行建连——坏 server 不阻塞好 server，总耗时 ≈ 最慢单 server。"""

    def test_one_bad_server_does_not_block_good_server(self):
        import asyncio

        from Django_xm.apps.tools import _load_mcp_tools_async

        async def good_fetch(srv):
            await asyncio.sleep(1.0)
            return [_fake_mcp_tool("good_tool")]

        async def bad_fetch(srv):
            await asyncio.sleep(0.3)
            raise RuntimeError("simulated server crash")

        async def _fetch_dispatcher(srv):
            # patch 检测到原函数为 async def 会生成 AsyncMock，
            # async side_effect 会被正确 await
            if srv.get("name") == "good":
                return await good_fetch(srv)
            return await bad_fetch(srv)

        servers = [{"name": "good", "transport": "http"}, {"name": "bad", "transport": "http"}]

        with (
            patch("Django_xm.apps.tools.mcp.is_mcp_available", return_value=True),
            patch(
                "Django_xm.apps.tools.mcp.resolve_servers.resolve_mcp_servers",
                return_value=servers,
            ),
            patch(
                "Django_xm.apps.tools.mcp.resolve_servers.fetch_tools_for_server",
                side_effect=_fetch_dispatcher,
            ),
        ):
            start = time.monotonic()
            tools = run_async(_load_mcp_tools_async())
            elapsed = time.monotonic() - start

        # 好的 server 工具正常装配 + metadata 标记
        self.assertEqual([t.name for t in tools], ["good_tool"])
        self.assertTrue(tools[0].metadata.get("is_mcp_tool"))
        # 总耗时 ≈ 单 server（max≈1.0s）而非相加（1.3s）：留 0.15s 调度余量
        self.assertLess(elapsed, 1.15, f"疑似串行: {elapsed:.3f}s")
        self.assertGreaterEqual(elapsed, 0.95, f"过早返回: {elapsed:.3f}s")


if __name__ == "__main__":
    unittest.main()
