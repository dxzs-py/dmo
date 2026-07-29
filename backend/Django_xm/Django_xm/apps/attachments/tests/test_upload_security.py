"""附件上传安全单元测试（Task 7）

覆盖 spec 1.6 节安全收紧项：
    1. validate_upload_file 拒绝伪装扩展名（用 .png 扩展名包装 PE/ELF 二进制）
    2. validate_upload_file 拒绝伪装扩展名（用 .pdf 扩展名包装 PNG 图片）
    3. validate_upload_file 通过合法图片
    4. validate_upload_file 通过合法文本
    5. validate_upload_file 拒绝超过 DATA_UPLOAD_MAX_MEMORY_SIZE 的文件
    6. check_user_storage_quota 在用户已用空间 + 新文件大小超限时拒绝
    7. ChatAttachmentUploadView.post 在超额时返回 413
    8. ChatAttachmentUploadView.post 在伪装扩展名时返回 400
    9. ChatAttachmentUploadView.post 在会话不存在时返回 404

设计说明：
    - 纯函数测试（magic bytes / quota）使用 unittest.TestCase，不需要 DB
    - 视图层测试使用 DRF APIRequestFactory + mock，避免依赖测试数据库
      （项目当前 approvals.0009_add_user_field 迁移存在依赖问题，
       无法创建测试 DB；待 Task 1 修复后可改为 Django TestCase）

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python manage.py test Django_xm.apps.attachments.tests.test_upload_security --verbosity=2
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

# Django 环境初始化（兼容 unittest 直接运行）
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIRequestFactory

from Django_xm.apps.attachments.services.attachment_validation import (
    _verify_magic_bytes,
    check_user_storage_quota,
    get_max_upload_size,
    get_user_total_attachment_size,
    validate_upload_file,
)
from Django_xm.apps.attachments.views import ChatAttachmentUploadView

# ============================================================================
# 测试用文件头
# ============================================================================

# PNG magic bytes: \x89PNG\r\n\x1a\n
_PNG_HEADER = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
# JPEG magic bytes: \xff\xd8\xff
_JPEG_HEADER = b"\xff\xd8\xff\xe0" + b"\x00" * 100
# PDF magic bytes: %PDF-1.4
_PDF_HEADER = b"%PDF-1.4\n" + b"\x00" * 100
# PE/Windows executable magic bytes: MZ
_PE_HEADER = b"MZ\x90\x00\x03" + b"\x00" * 100
# ELF magic bytes: \x7fELF
_ELF_HEADER = b"\x7fELF\x02\x01\x01" + b"\x00" * 100
# ZIP magic bytes: PK\x03\x04
_ZIP_HEADER = b"PK\x03\x04" + b"\x00" * 100


def _make_file(name: str, content: bytes) -> SimpleUploadedFile:
    """构造 SimpleUploadedFile，content 作为完整文件内容"""
    return SimpleUploadedFile(name=name, content=content, content_type="application/octet-stream")


def _make_user(user_id: int = 1, is_staff: bool = False, is_authenticated: bool = True):
    """构造 mock 用户"""
    user = MagicMock()
    user.id = user_id
    user.pk = user_id
    user.is_staff = is_staff
    user.is_authenticated = is_authenticated
    user.username = "testuser"
    return user


# ============================================================================
# Task 7.1：max_size 来自 settings
# ============================================================================


class MaxUploadSizeSourceTests(unittest.TestCase):
    """验证 get_max_upload_size 从 settings 读取，不硬编码。"""

    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=2 * 1024 * 1024)
    def test_uses_data_upload_max_memory_size(self):
        """优先使用 settings.DATA_UPLOAD_MAX_MEMORY_SIZE"""
        self.assertEqual(get_max_upload_size(), 2 * 1024 * 1024)

    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=None, FILE_UPLOAD_MAX_MEMORY_SIZE=5 * 1024 * 1024)
    def test_fallback_to_file_upload_max_memory_size(self):
        """DATA_UPLOAD_MAX_MEMORY_SIZE 缺失时回退到 FILE_UPLOAD_MAX_MEMORY_SIZE"""
        self.assertEqual(get_max_upload_size(), 5 * 1024 * 1024)


# ============================================================================
# Task 7.2：magic bytes 校验
# ============================================================================


class MagicBytesValidationTests(unittest.TestCase):
    """验证 _verify_magic_bytes 拒绝伪装扩展名。

    攻击场景：用户把可执行文件 / 图片改名为 .png / .pdf 等扩展名，
    试图绕过扩展名白名单。
    """

    def test_png_extension_with_pe_content_rejected(self):
        """PE 可执行文件伪装成 .png 被拒绝"""
        f = _make_file("malware.png", _PE_HEADER)
        is_valid, msg = _verify_magic_bytes(f, "png")
        self.assertFalse(is_valid, f"伪装扩展名 .png 包装 PE 应被拒绝, msg={msg}")

    def test_png_extension_with_elf_content_rejected(self):
        """ELF 可执行文件伪装成 .png 被拒绝"""
        f = _make_file("malware.png", _ELF_HEADER)
        is_valid, msg = _verify_magic_bytes(f, "png")
        self.assertFalse(is_valid, f"伪装扩展名 .png 包装 ELF 应被拒绝, msg={msg}")

    def test_pdf_extension_with_png_content_rejected(self):
        """PNG 图片伪装成 .pdf 被拒绝"""
        f = _make_file("image_as_pdf.pdf", _PNG_HEADER)
        is_valid, msg = _verify_magic_bytes(f, "pdf")
        self.assertFalse(is_valid, f"伪装扩展名 .pdf 包装 PNG 应被拒绝, msg={msg}")

    def test_png_extension_with_zip_content_rejected(self):
        """ZIP 文件伪装成 .png 被拒绝"""
        f = _make_file("archive.png", _ZIP_HEADER)
        is_valid, msg = _verify_magic_bytes(f, "png")
        self.assertFalse(is_valid, f"伪装扩展名 .png 包装 ZIP 应被拒绝, msg={msg}")

    def test_jpg_extension_with_pdf_content_rejected(self):
        """PDF 文件伪装成 .jpg 被拒绝"""
        f = _make_file("evil.jpg", _PDF_HEADER)
        is_valid, msg = _verify_magic_bytes(f, "jpg")
        self.assertFalse(is_valid, f"伪装扩展名 .jpg 包装 PDF 应被拒绝, msg={msg}")

    def test_legitimate_png_accepted(self):
        """合法 PNG 通过校验"""
        f = _make_file("ok.png", _PNG_HEADER)
        is_valid, msg = _verify_magic_bytes(f, "png")
        self.assertTrue(is_valid, f"合法 PNG 应通过校验, msg={msg}")

    def test_legitimate_jpeg_accepted(self):
        """合法 JPEG 通过校验"""
        f = _make_file("ok.jpg", _JPEG_HEADER)
        is_valid, msg = _verify_magic_bytes(f, "jpg")
        self.assertTrue(is_valid, f"合法 JPEG 应通过校验, msg={msg}")

    def test_legitimate_pdf_accepted(self):
        """合法 PDF 通过校验"""
        f = _make_file("ok.pdf", _PDF_HEADER)
        is_valid, msg = _verify_magic_bytes(f, "pdf")
        self.assertTrue(is_valid, f"合法 PDF 应通过校验, msg={msg}")

    def test_text_extension_with_text_content_accepted(self):
        """txt 扩展名 + 纯文本内容通过校验（filetype 无法识别但属文本类）"""
        f = _make_file("ok.txt", b"hello world\n")
        is_valid, msg = _verify_magic_bytes(f, "txt")
        self.assertTrue(is_valid, f"合法 txt 应通过校验, msg={msg}")

    def test_unmanaged_extension_passes(self):
        """未在 VALIDATED_MIME_MAP 中的扩展名直接放行"""
        f = _make_file("ok.doc", b"some content")
        is_valid, _msg = _verify_magic_bytes(f, "doc")
        self.assertTrue(is_valid)


class ValidateUploadFileIntegrationTests(unittest.TestCase):
    """validate_upload_file 端到端校验链测试"""

    def test_disguised_pe_as_png_rejected(self):
        """PE 文件伪装成 .png 在 validate_upload_file 层被拒绝"""
        f = _make_file("evil.png", _PE_HEADER)
        is_valid, msg, _mime = validate_upload_file(f)
        self.assertFalse(is_valid)
        self.assertIn("不匹配", msg)

    def test_disguised_zip_as_pdf_rejected(self):
        """ZIP 文件伪装成 .pdf 在 validate_upload_file 层被拒绝"""
        f = _make_file("evil.pdf", _ZIP_HEADER)
        is_valid, msg, _mime = validate_upload_file(f)
        self.assertFalse(is_valid)
        self.assertIn("不匹配", msg)

    def test_legitimate_png_accepted(self):
        """合法 PNG 在 validate_upload_file 层通过"""
        f = _make_file("ok.png", _PNG_HEADER)
        is_valid, msg, mime = validate_upload_file(f)
        self.assertTrue(is_valid, f"合法 PNG 应通过, msg={msg}")
        self.assertEqual(mime, "image/png")

    def test_oversized_file_rejected(self):
        """超过 settings.DATA_UPLOAD_MAX_MEMORY_SIZE 的文件被拒绝"""
        # 构造一个超过默认 10MB 的文件
        big_content = b"\x00" * (11 * 1024 * 1024)
        f = _make_file("big.png", big_content)
        is_valid, msg, _mime = validate_upload_file(f)
        self.assertFalse(is_valid)
        self.assertIn("文件大小", msg)

    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=2 * 1024 * 1024)
    def test_max_size_from_settings_dynamic(self):
        """校验上限随 settings 动态变化（验证未硬编码 10MB）"""
        # 3MB 文件 > 2MB 上限
        content = b"\x89PNG\r\n\x1a\n" + b"\x00" * (3 * 1024 * 1024)
        f = _make_file("ok.png", content)
        is_valid, msg, _mime = validate_upload_file(f)
        self.assertFalse(is_valid)
        self.assertIn("2MB", msg)


# ============================================================================
# Task 7.3：用户总存储配额校验
# ============================================================================


class UserStorageQuotaTests(unittest.TestCase):
    """验证 check_user_storage_quota / get_user_total_attachment_size"""

    def test_unauthenticated_user_returns_zero(self):
        """未认证用户查询返回 0"""
        anon_user = MagicMock()
        anon_user.is_authenticated = False
        self.assertEqual(get_user_total_attachment_size(anon_user), 0)

    def test_none_user_returns_zero(self):
        """None user 返回 0"""
        self.assertEqual(get_user_total_attachment_size(None), 0)

    @override_settings(ATTACHMENT_MAX_TOTAL_SIZE_MB=1)
    def test_quota_exceeded_rejected(self):
        """用户已用 + 新文件超过 ATTACHMENT_MAX_TOTAL_SIZE_MB 时拒绝"""
        user = _make_user()
        # 模拟已用 0.5MB
        with patch(
            "Django_xm.apps.attachments.services.attachment_validation.get_user_total_attachment_size",
            return_value=0.5 * 1024 * 1024,
        ):
            # 新上传 0.6MB → 0.5 + 0.6 = 1.1MB > 1MB
            is_ok, msg, _current = check_user_storage_quota(user, 0.6 * 1024 * 1024)
        self.assertFalse(is_ok, f"超额应被拒绝, msg={msg}")
        self.assertIn("存储空间不足", msg)

    @override_settings(ATTACHMENT_MAX_TOTAL_SIZE_MB=10)
    def test_quota_within_limit_accepted(self):
        """用户已用 + 新文件未超过上限时通过"""
        user = _make_user()
        with patch(
            "Django_xm.apps.attachments.services.attachment_validation.get_user_total_attachment_size",
            return_value=2 * 1024 * 1024,
        ):
            is_ok, msg, _current = check_user_storage_quota(user, 1 * 1024 * 1024)
        self.assertTrue(is_ok)
        self.assertEqual(msg, "")

    @override_settings(ATTACHMENT_MAX_TOTAL_SIZE_MB=5)
    def test_quota_boundary_exact_limit_accepted(self):
        """正好等于上限时通过（<=）"""
        user = _make_user()
        with patch(
            "Django_xm.apps.attachments.services.attachment_validation.get_user_total_attachment_size",
            return_value=4 * 1024 * 1024,
        ):
            is_ok, _msg, _current = check_user_storage_quota(user, 1 * 1024 * 1024)
        self.assertTrue(is_ok)


# ============================================================================
# Task 7.3 + 7.5：ChatAttachmentUploadView 视图层测试（无 DB）
# ============================================================================


class ChatAttachmentUploadViewTests(unittest.TestCase):
    """ChatAttachmentUploadView 视图层测试

    使用 DRF APIRequestFactory + mock，不依赖测试数据库。
    覆盖：
        - 伪装扩展名返回 400
        - 超额上传返回 413
        - 合法文件成功返回 201
        - 会话不存在返回 404
    """

    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = _make_user(user_id=1)
        self.session = MagicMock()
        self.session.session_id = "sess-test-1"
        self.session.user_id = self.user.id

    def _make_post_request(self, session_id: str, file=None):
        """构造 POST 请求"""
        data = {}
        if file is not None:
            data["file"] = file
        request = self.factory.post(
            f"/api/v1/attachments/sessions/{session_id}/upload/",
            data=data,
            format="multipart",
        )
        request.user = self.user
        return request

    def test_disguised_extension_returns_400(self):
        """伪装扩展名 .png 包装 PE 文件被视图层拒绝（400）"""
        with (
            patch(
                "Django_xm.apps.attachments.views.check_user_storage_quota",
                return_value=(True, "", 0),
            ),
            patch(
                "Django_xm.apps.chat.services.cross_app.get_chat_session_strict",
                return_value=self.session,
            ),
        ):
            request = self._make_post_request(
                self.session.session_id,
                file=_make_file("evil.png", _PE_HEADER),
            )
            response = ChatAttachmentUploadView.as_view()(request, self.session.session_id)
            response.render()
        self.assertEqual(
            response.status_code,
            400,
            f"伪装扩展名应返回 400, actual={response.status_code}, body={response.content[:300]}",
        )

    @override_settings(ATTACHMENT_MAX_TOTAL_SIZE_MB=1, DATA_UPLOAD_MAX_MEMORY_SIZE=2 * 1024 * 1024)
    def test_quota_exceeded_returns_413(self):
        """超额上传被视图层拒绝（413）

        模拟场景：用户已用 0.6MB，上传 0.6MB → 1.2MB > 1MB
        """
        # Mock quota 直接拒绝
        with (
            patch(
                "Django_xm.apps.attachments.views.check_user_storage_quota",
                return_value=(False, "存储空间不足：已使用 0.6MB / 上限 1MB", 0.6 * 1024 * 1024),
            ),
            patch(
                "Django_xm.apps.chat.services.cross_app.get_chat_session_strict",
                return_value=self.session,
            ),
        ):
            request = self._make_post_request(
                self.session.session_id,
                file=_make_file("big.png", _PNG_HEADER + b"\x00" * int(0.6 * 1024 * 1024)),
            )
            response = ChatAttachmentUploadView.as_view()(request, self.session.session_id)
            response.render()
        self.assertEqual(
            response.status_code,
            413,
            f"超额上传应返回 413, actual={response.status_code}, body={response.content[:300]}",
        )

    def test_session_not_found_returns_404(self):
        """会话不存在返回 404"""
        with patch(
            "Django_xm.apps.chat.services.cross_app.get_chat_session_strict",
            side_effect=Exception("session not found"),
        ):
            request = self._make_post_request(
                "nonexistent",
                file=_make_file("ok.png", _PNG_HEADER),
            )
            response = ChatAttachmentUploadView.as_view()(request, "nonexistent")
            response.render()
        self.assertEqual(response.status_code, 404)

    def test_missing_file_returns_400(self):
        """缺少 file 字段返回 400"""
        with patch(
            "Django_xm.apps.chat.services.cross_app.get_chat_session_strict",
            return_value=self.session,
        ):
            request = self._make_post_request(self.session.session_id, file=None)
            response = ChatAttachmentUploadView.as_view()(request, self.session.session_id)
            response.render()
        self.assertEqual(response.status_code, 400)

    def test_legitimate_png_upload_success(self):
        """合法 PNG 上传成功（201）"""
        mock_attachment = MagicMock()
        mock_attachment.id = 1
        mock_attachment.original_name = "ok.png"
        mock_attachment.file_size = len(_PNG_HEADER)
        mock_attachment.file_type = "png"
        mock_attachment.mime_type = "image/png"
        mock_attachment.created_at.isoformat.return_value = "2026-07-27T10:00:00Z"
        mock_attachment.message_id = None
        # serialize_attachment 会访问 att.file.url
        mock_file = MagicMock()
        mock_file.url = "/media/test/ok.png"
        mock_attachment.file = mock_file

        with (
            patch(
                "Django_xm.apps.attachments.views.check_user_storage_quota",
                return_value=(True, "", 0),
            ),
            patch(
                "Django_xm.apps.chat.services.cross_app.get_chat_session_strict",
                return_value=self.session,
            ),
            patch(
                "Django_xm.apps.attachments.views.ChatAttachment.objects.create",
                return_value=mock_attachment,
            ),
            patch(
                "Django_xm.apps.attachments.views.serialize_attachment",
                return_value={"id": 1, "original_name": "ok.png"},
            ),
            patch(
                "Django_xm.apps.attachments.services.attachment_lifecycle.AttachmentLifecycleService.record_file_hash",
                return_value=None,
            ),
        ):
            request = self._make_post_request(
                self.session.session_id,
                file=_make_file("ok.png", _PNG_HEADER),
            )
            response = ChatAttachmentUploadView.as_view()(request, self.session.session_id)
            response.render()
        self.assertEqual(
            response.status_code,
            201,
            f"合法 PNG 上传应返回 201, actual={response.status_code}, body={response.content[:300]}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
