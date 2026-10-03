"""F-02 路径穿越回归测试：file_manager containment + research 下载/预览端点兜底。

漏洞背景：``FileManagerService.get_file_info`` 原先仅做词法 parts 查
"sandbox"，``task_dir / relative_path`` 中 ``relative_path`` 含 ``..`` /
绝对路径 / 盘符形态时整体放行，配合 ``<path:filename>`` 路由构成任意文件读。

覆盖：
1. service 层（FileManagerContainmentTests）：``..\\..\\..\\.env``、``../``
   （即 URL ``..%2F..%2F`` 解码形态）、``..%5C`` 解码形态、绝对路径
   ``D:\\``、``/etc/passwd`` 形态、盘符大小写、兄弟任务目录（边界外拒绝）、
   合法子路径放行、sandbox 目录排除、list/write 越界拒绝；
2. 视图层（ResearchFileViewGuardTests）：下载/预览端点 filename 净化兜底
   （400）与合法文件 200（Content-Disposition 转义由 quote() 完成）。

说明：URL 中 ``%2F``/``%5C`` 在 ASGI/WSGI 层解码后才进入视图，故用解码
形态直接构造；测试另以 unquote 复现编码变体折叠为穿越字符串。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.research.tests.test_file_download_security
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from Django_xm.apps.core.services.file_manager import FileManagerService, is_safe_relative_filename
from Django_xm.apps.research.models import ResearchTask
from Django_xm.apps.research.views_files import (
    DeepResearchFileContentView,
    DeepResearchFileDownloadView,
)

User = get_user_model()
_VIEWS_MODULE = "Django_xm.apps.research.views_files"


def _consume_and_close(response):
    """消费流式响应并关闭句柄，返回文件字节内容（非流式响应返回 None）。

    注意：FileResponse.close() 会发送 request_finished 信号，其接收器
    close_old_connections 会在 Django TestCase 的原子块内关闭当前 DB 连接
    （CONN_MAX_AGE=60 下连接被判定 obsolete/errors_occurred 即 close），
    导致同测试类后续用例报 "the connection is closed"。故仅在消费/关闭
    窗口内临时断开该接收器，测试结束前恢复。
    """
    from django.core import signals
    from django.db import close_old_connections

    content = None
    signals.request_finished.disconnect(close_old_connections)
    try:
        if getattr(response, "streaming_content", None) is not None:
            content = b"".join(response.streaming_content)
        response.close()
    finally:
        signals.request_finished.connect(close_old_connections)
    return content


def _make_task_tree(tmp_root: Path) -> Path:
    """构建 {tmp_root}/data/research/t1 任务目录：合法文件 + sandbox 内部文件。"""
    task_dir = tmp_root / "data" / "research" / "t1"
    (task_dir / "report").mkdir(parents=True, exist_ok=True)
    (task_dir / "report" / "report.md").write_text("# legal report", encoding="utf-8")
    (task_dir / "note.txt").write_text("bare name file", encoding="utf-8")
    (task_dir / "sandbox").mkdir(parents=True, exist_ok=True)
    (task_dir / "sandbox" / "x.txt").write_text("sandbox internal", encoding="utf-8")
    return task_dir


class FileManagerContainmentTests(unittest.TestCase):
    """FileManagerService resolve + is_relative_to 强 containment（不依赖 DB）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        # 越界秘密文件：从任务目录 {tmp}/data/research/t1 三级 .. 即达 {tmp}/.env
        self.secret = self.tmp_root / ".env"
        self.secret.write_text("SECRET_KEY=leak", encoding="utf-8")
        self.task_dir = _make_task_tree(self.tmp_root)
        self.service = FileManagerService(base_dir=self.tmp_root / "data")

    # ── get_file_info：穿越拒绝 ──────────────────────────────────
    def test_traversal_backslash_rejected(self):
        """``..\\..\\..\\.env``（..%5C 解码形态）→ None。"""
        self.assertIsNone(self.service.get_file_info("t1", "..\\..\\..\\.env", "research"))

    def test_traversal_slash_rejected(self):
        """``../../../.env``（..%2F 解码形态）→ None。"""
        self.assertIsNone(self.service.get_file_info("t1", "../../../.env", "research"))

    def test_url_encoded_variant_folds_to_traversal(self):
        """unquote("..%2F..%2F..%2F.env") 即路由层实际收到的穿越串 → None。"""
        decoded = unquote("..%2F..%2F..%2F.env")
        self.assertEqual(decoded, "../../../.env")
        self.assertIsNone(self.service.get_file_info("t1", decoded, "research"))

    def test_absolute_windows_path_rejected(self):
        """``D:\\...\\.env`` 绝对路径（task_dir / 绝对路径 = 整体替换）→ None。"""
        self.assertIsNone(self.service.get_file_info("t1", str(self.secret), "research"))

    def test_root_anchored_path_rejected(self):
        """/etc/passwd 根锚定形态 → None。"""
        self.assertIsNone(self.service.get_file_info("t1", "/etc/passwd", "research"))

    @staticmethod
    def _flip_drive_case(path: Path) -> str | None:
        """翻转盘符大小写（如 D: → d:）；无盘符时返回 None。"""
        drive = path.drive
        if not drive:
            return None
        flipped = drive.lower() if drive != drive.lower() else drive.upper()
        return flipped + str(path)[len(drive) :]

    @unittest.skipUnless(os.name == "nt", "Windows 盘符语义")
    def test_drive_case_variant_rejected(self):
        """盘符大小写变体（d: vs D:）经 resolve 归一后仍判越界 → None。"""
        variant = self._flip_drive_case(self.secret)
        self.assertIsNotNone(variant)
        self.assertNotEqual(variant, str(self.secret))
        self.assertIsNone(self.service.get_file_info("t1", variant, "research"))

    def test_sibling_task_dir_rejected(self):
        """兄弟任务目录（task_dir 边界外）→ None。"""
        other = self.tmp_root / "data" / "research" / "t2"
        other.mkdir(parents=True, exist_ok=True)
        (other / "leak.txt").write_text("other task", encoding="utf-8")
        self.assertIsNone(self.service.get_file_info("t1", "../t2/leak.txt", "research"))

    def test_traversal_to_nonexistent_target_rejected(self):
        """穿越至不存在路径 → None（不得因 exists() 为假而落入按名搜索）。"""
        self.assertIsNone(self.service.get_file_info("t1", "..\\..\\..\\nope.txt", "research"))

    # ── get_file_info：合法路径放行 ─────────────────────────────
    def test_legal_subpath_allowed(self):
        """合法子路径 report/report.md → FileInfo。"""
        file_info = self.service.get_file_info("t1", "report/report.md", "research")
        self.assertIsNotNone(file_info)
        self.assertEqual(file_info.path.name, "report.md")

    def test_legal_bare_name_fallback_allowed(self):
        """纯文件名兜底（rglob 按名匹配，候选 containment 复核）→ FileInfo。"""
        file_info = self.service.get_file_info("t1", "report.md", "research")
        self.assertIsNotNone(file_info)
        self.assertEqual(file_info.path.name, "report.md")

    def test_sandbox_dir_excluded(self):
        """sandbox 子目录保持排除语义 → None。"""
        self.assertIsNone(self.service.get_file_info("t1", "sandbox/x.txt", "research"))
        self.assertIsNone(self.service.get_file_info("t1", "x.txt", "research"))

    @unittest.skipUnless(os.name == "nt", "Windows 盘符语义")
    def test_inside_task_dir_absolute_with_case_variant_allowed(self):
        """任务目录内绝对路径（盘符大小写不同）resolve 归一后仍在边界内 → 放行。"""
        variant = self._flip_drive_case(self.task_dir / "note.txt")
        self.assertIsNotNone(variant)
        file_info = self.service.get_file_info("t1", variant, "research")
        self.assertIsNotNone(file_info)

    # ── read_file_content：读取入口同链路 ───────────────────────
    def test_read_file_content_traversal_rejected(self):
        self.assertIsNone(self.service.read_file_content("t1", "../../../.env", "research"))

    # ── list_task_files：subdirectory 越界 ──────────────────────
    def test_list_subdirectory_traversal_rejected(self):
        """subdirectory 含 .. → 拒绝，返回空列表且不越界枚举。"""
        self.assertEqual(self.service.list_task_files("t1", "research", "../../.."), [])

    def test_list_legal_subdirectory_allowed(self):
        files = self.service.list_task_files("t1", "research", "report")
        self.assertEqual([f.path.name for f in files], ["report.md"])

    # ── write_file_content：越界写拒绝 ──────────────────────────
    def test_write_traversal_rejected(self):
        """写路径越界 → False 且不在边界外落盘。"""
        self.assertFalse(self.service.write_file_content("t1", "../../../evil.txt", "pwn", "research"))
        self.assertFalse((self.tmp_root / "evil.txt").exists())

    def test_write_legal_path_allowed(self):
        self.assertTrue(self.service.write_file_content("t1", "notes/new.md", "ok", "research"))
        self.assertTrue((self.task_dir / "notes" / "new.md").exists())


class IsSafeRelativeFilenameTests(unittest.TestCase):
    """视图层 filename 净化函数单元用例。"""

    def test_traversal_forms_rejected(self):
        for name in [
            "..\\..\\..\\.env",
            "../../../.env",
            "a/../b.txt",
            "..",
        ]:
            with self.subTest(name=name):
                self.assertFalse(is_safe_relative_filename(name))

    def test_double_encoded_form_not_misjudged(self):
        """双重编码（%252F → 视图收到字面 ``..%2F..%2F``）无 .. 段，不误拒。"""
        self.assertTrue(is_safe_relative_filename("..%2F..%2F"))

    def test_absolute_forms_rejected(self):
        self.assertFalse(is_safe_relative_filename("/etc/passwd"))  # POSIX 绝对 / Windows 根锚定

    @unittest.skipUnless(os.name == "nt", "Windows 盘符/UNC 语义")
    def test_windows_absolute_forms_rejected(self):
        for name in ["D:\\Windows\\win.ini", "D:relative", "\\\\srv\\share\\x", "C:\\x"]:
            with self.subTest(name=name):
                self.assertFalse(is_safe_relative_filename(name))

    def test_empty_rejected(self):
        for name in ["", "   ", None]:
            with self.subTest(name=name):
                self.assertFalse(is_safe_relative_filename(name))

    def test_legal_names_allowed(self):
        for name in ["report.md", "report/report.md", "notes/deep/sub/a.txt", ".env"]:
            with self.subTest(name=name):
                self.assertTrue(is_safe_relative_filename(name))


class ResearchFileViewGuardTests(TestCase):
    """research 下载/预览视图：filename 净化兜底（400）+ 合法文件放行（200/404）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="f02_research_user", password="x")
        ResearchTask.objects.create(task_id="t1", query="test", created_by=self.user)
        self.factory = APIRequestFactory()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.secret = self.tmp_root / ".env"
        self.secret.write_text("SECRET_KEY=leak", encoding="utf-8")
        _make_task_tree(self.tmp_root)
        patcher = mock.patch(f"{_VIEWS_MODULE}.file_manager", FileManagerService(base_dir=self.tmp_root / "data"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _get_download(self, filename):
        request = self.factory.get("/api/v1/research/tasks/t1/file/download/x/")
        force_authenticate(request, user=self.user)
        return DeepResearchFileDownloadView.as_view()(request, task_id="t1", filename=filename)

    def _get_content(self, filename):
        request = self.factory.get("/api/v1/research/tasks/t1/file/content/x/")
        force_authenticate(request, user=self.user)
        return DeepResearchFileContentView.as_view()(request, task_id="t1", filename=filename)

    def test_download_traversal_rejected(self):
        """穿越/绝对路径 filename → 400（视图净化兜底），不返回文件内容。"""
        for filename in [
            "..\\..\\..\\.env",
            "../../../.env",
            str(self.secret),
            "/etc/passwd",
        ]:
            with self.subTest(filename=filename):
                response = self._get_download(filename)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn(b"SECRET_KEY=leak", response.rendered_content)

    def test_download_legal_subpath_ok(self):
        """合法子路径下载 → 200，Content-Disposition 为 attachment 且文件名已转义。"""
        response = self._get_download("report/report.md")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(_consume_and_close(response), b"# legal report")

    def test_download_bare_name_fallback_ok(self):
        response = self._get_download("report.md")
        self.assertEqual(response.status_code, 200)
        _consume_and_close(response)

    def test_download_sandbox_excluded(self):
        response = self._get_download("sandbox/x.txt")
        self.assertEqual(response.status_code, 404)

    def test_content_view_traversal_rejected(self):
        response = self._get_content("../../../.env")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(b"SECRET_KEY=leak", response.rendered_content)

    def test_content_view_legal_ok(self):
        response = self._get_content("report/report.md")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["content"], "# legal report")

    def test_other_user_task_denied(self):
        """属主校验：其他用户访问任务 → 404（回归既有 IDOR 防线）。"""
        other_user = User.objects.create_user(username="f02_other_user", password="x")
        request = self.factory.get("/api/v1/research/tasks/t1/file/download/x/")
        force_authenticate(request, user=other_user)
        response = DeepResearchFileDownloadView.as_view()(request, task_id="t1", filename="report.md")
        self.assertEqual(response.status_code, 404)
