"""file_reader / attachment_reader 路径安全与属主校验测试（S-03/S-06/S-09）。

覆盖：
1. read_file_content：DATA_DIR 外绝对路径拒绝、DATA_DIR 内正常读取、
   敏感文件清单命中拒绝；
2. cross_app.get_attachment_by_id 属主过滤（IDOR）：用户 A 查询不到用户 B 的附件，
   user=None 系统内部链路保持旧行为；
3. attachment_reader 工具：无属主上下文拒绝读取、跨用户附件拒绝、
   属主附件正常读取。

运行：
    python manage.py test Django_xm.apps.tools.tests.test_file_reader_security
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from Django_xm.apps.attachments.services.cross_app import get_attachment_by_id
from Django_xm.apps.tools.langchain.file_reader import (
    AttachmentReaderTool,
    read_file_content,
)
from Django_xm.apps.tools.langchain.filesystem import check_path_allowed

_MODULE = "Django_xm.apps.tools.langchain.filesystem"
_FILE_READER_MODULE = "Django_xm.apps.tools.langchain.file_reader"

User = get_user_model()


def _mock_data_dir(tmp: str):
    return mock.patch(f"{_MODULE}._resolve_data_dir", return_value=tmp)


class TestReadFileContentBoundary(TestCase):
    """read_file_content 路径硬边界（file_reader 工具底层读取函数）。"""

    def test_outside_data_dir_rejected(self):
        """DATA_DIR 外绝对路径 → ValueError，不读文件内容。"""
        with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as data_dir:
            secret = Path(outside) / "secret.txt"
            secret.write_text("top secret", encoding="utf-8")
            with _mock_data_dir(data_dir):
                with self.assertRaises(ValueError) as ctx:
                    read_file_content(str(secret))
            self.assertIn("拒绝访问", str(ctx.exception))
            self.assertNotIn("top secret", str(ctx.exception))

    def test_sensitive_file_rejected(self):
        """DATA_DIR 内 .env / settings.py / *.pem 命中敏感清单 → 拒绝。"""
        cases = [".env", "settings.py", "server.pem", "id_rsa"]
        for name in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as data_dir:
                    target = Path(data_dir) / "media" / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("SECRET_TOKEN=xxx", encoding="utf-8")
                    with _mock_data_dir(data_dir):
                        with self.assertRaises(ValueError) as ctx:
                            read_file_content(str(target))
                    self.assertIn("敏感文件保护清单", str(ctx.exception))

    def test_inside_data_dir_normal_read(self):
        """DATA_DIR 内普通文本文件 → 正常读取（附件/深研产物链路不受影响）。"""
        with tempfile.TemporaryDirectory() as data_dir:
            target = Path(data_dir) / "media" / "attachments" / "report.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("hello content", encoding="utf-8")
            with _mock_data_dir(data_dir):
                self.assertEqual(read_file_content(str(target)), "hello content")

    def test_check_path_allowed_anchor_mode(self):
        """check_path_allowed：anchor（workspace）内放行、越界拒绝。"""
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as outside:
            self.assertIsNone(check_path_allowed(Path(workspace) / "a.md", anchor=Path(workspace)))
            self.assertIsNotNone(check_path_allowed(Path(outside) / "a.md", anchor=Path(workspace)))


class TestAttachmentOwnerFilter(TestCase):
    """cross_app.get_attachment_by_id 属主过滤（S-09 IDOR）。"""

    def setUp(self):
        self.user_a = User.objects.create_user(username="user_a", password="x")
        self.user_b = User.objects.create_user(username="user_b", password="x")
        from Django_xm.apps.chat.models import ChatSession

        self.session_a = ChatSession.objects.create(session_id="sess-a", user=self.user_a)
        self.session_b = ChatSession.objects.create(session_id="sess-b", user=self.user_b)
        from Django_xm.apps.attachments.models import ChatAttachment

        # file 字段直接赋相对名，不触发磁盘写入（本测试聚焦查询过滤）
        self.attachment_a = ChatAttachment.objects.create(
            session=self.session_a,
            file="chat_attachments/a/report.txt",
            original_name="report.txt",
            file_size=10,
        )

    def test_owner_can_get_attachment(self):
        """属主查询命中。"""
        attachment = get_attachment_by_id(self.attachment_a.id, user=self.user_a)
        self.assertIsNotNone(attachment)
        self.assertEqual(attachment.id, self.attachment_a.id)

    def test_other_user_cannot_get_attachment(self):
        """用户 B 查询用户 A 的附件 → None（等价 404）。"""
        self.assertIsNone(get_attachment_by_id(self.attachment_a.id, user=self.user_b))

    def test_no_user_keeps_legacy_behavior(self):
        """user=None（系统内部链路，ID 来自服务端查询）保持旧行为。"""
        attachment = get_attachment_by_id(self.attachment_a.id, user=None)
        self.assertIsNotNone(attachment)

    def test_nonexistent_id_returns_none(self):
        self.assertIsNone(get_attachment_by_id(999999, user=self.user_a))


class TestAttachmentReaderToolOwnerCheck(TestCase):
    """attachment_reader 工具属主强制校验。"""

    def setUp(self):
        self.tool = AttachmentReaderTool()
        self.user_a = User.objects.create_user(username="tool_user_a", password="x")
        self.user_b = User.objects.create_user(username="tool_user_b", password="x")
        from Django_xm.apps.chat.models import ChatSession
        from Django_xm.apps.attachments.models import ChatAttachment

        self.session_a = ChatSession.objects.create(session_id="tool-sess-a", user=self.user_a)
        self.attachment_a = ChatAttachment.objects.create(
            session=self.session_a,
            file="chat_attachments/a/notes.txt",
            original_name="notes.txt",
            file_size=10,
        )

    def _capture_user(self, user_id):
        self.tool._capture_configurable({"configurable": {"user_id": user_id}})

    def test_missing_user_context_rejected(self):
        """configurable 未注入 user_id（无法确认属主）→ 拒绝读取。"""
        result = self.tool._run(self.attachment_a.id)
        self.assertIn("无法确认当前用户身份", result)

    def test_cross_user_attachment_rejected(self):
        """用户 B 读用户 A 的附件 → 读取失败（属主过滤后等价 404）。"""
        self._capture_user(self.user_b.id)
        result = self.tool._run(self.attachment_a.id)
        self.assertIn("读取附件失败", result)

    def test_owner_attachment_read_ok(self):
        """属主读取 → 通过（文件存在性与读取内容 mock 掉，聚焦属主链路）。"""
        self._capture_user(self.user_a.id)
        with (
            mock.patch(f"{_FILE_READER_MODULE}.read_file_content", return_value="hello"),
            mock.patch(f"{_FILE_READER_MODULE}.os.path.exists", return_value=True),
        ):
            result = self.tool._run(self.attachment_a.id)
        self.assertIn("hello", result)
        self.assertIn(self.attachment_a.original_name, result)
