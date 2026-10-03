"""F-02 路径穿越回归测试：workflow 下载/预览端点 filename 兜底 + containment。

与 research 侧 test_file_download_security.py 同源漏洞（file_manager
get_file_info 词法 containment 缺失 + ``<path:filename>`` 路由），本文件
覆盖 learning（workflow）两个端点的视图净化与 service 联动：
- 穿越/绝对路径 filename → 400（视图 is_safe_relative_filename 兜底）；
- service 层返回 None 的形态 → 404（sandbox 排除）；
- 合法文件 → 200，下载响应 Content-Disposition 由 quote() 完成转义。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.learning.tests.test_file_download_security
"""

import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from Django_xm.apps.core.services.file_manager import FileManagerService
from Django_xm.apps.learning.views import WorkflowFileContentView, WorkflowFileDownloadView

from .test_learning_views import _make_session

User = get_user_model()
_VIEWS_MODULE = "Django_xm.apps.learning.views"


def _consume_and_close(response):
    """消费流式响应并关闭句柄（request_finished 接收器处理细节见
    research/tests/test_file_download_security.py 同名函数注释）。"""
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


class WorkflowFileViewGuardTests(TestCase):
    """workflow 文件端点：filename 净化兜底 + 合法文件放行。"""

    def setUp(self):
        self.user = User.objects.create_user(username="f02_wf_user", password="x")
        _make_session(self.user, "t1")
        self.factory = APIRequestFactory()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.secret = self.tmp_root / ".env"
        self.secret.write_text("SECRET_KEY=leak", encoding="utf-8")
        task_dir = self.tmp_root / "data" / "workflow" / "t1"
        (task_dir / "report").mkdir(parents=True, exist_ok=True)
        (task_dir / "report" / "report.md").write_text("# legal report", encoding="utf-8")
        (task_dir / "sandbox").mkdir(parents=True, exist_ok=True)
        (task_dir / "sandbox" / "x.txt").write_text("sandbox internal", encoding="utf-8")
        patcher = mock.patch(f"{_VIEWS_MODULE}.file_manager", FileManagerService(base_dir=self.tmp_root / "data"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _get_download(self, filename):
        request = self.factory.get("/api/v1/learning/workflows/t1/files/download/x/")
        force_authenticate(request, user=self.user)
        return WorkflowFileDownloadView.as_view()(request, thread_id="t1", filename=filename)

    def _get_content(self, filename):
        request = self.factory.get("/api/v1/learning/workflows/t1/files/content/x/")
        force_authenticate(request, user=self.user)
        return WorkflowFileContentView.as_view()(request, thread_id="t1", filename=filename)

    def test_download_traversal_rejected(self):
        """``..\\..\\..\\.env`` / ``../`` / 绝对路径 / 根锚定 → 400，不泄露内容。"""
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
        response = self._get_download("report/report.md")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(_consume_and_close(response), b"# legal report")

    def test_download_sandbox_excluded(self):
        self.assertEqual(self._get_download("sandbox/x.txt").status_code, 404)

    def test_content_view_traversal_rejected(self):
        response = self._get_content("../../../.env")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(b"SECRET_KEY=leak", response.rendered_content)

    def test_content_view_legal_ok(self):
        response = self._get_content("report/report.md")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["content"], "# legal report")

    def test_other_user_session_denied(self):
        """属主校验：其他用户访问会话 → 404（回归既有 IDOR 防线）。"""
        other_user = User.objects.create_user(username="f02_wf_other", password="x")
        request = self.factory.get("/api/v1/learning/workflows/t1/files/download/x/")
        force_authenticate(request, user=other_user)
        response = WorkflowFileDownloadView.as_view()(request, thread_id="t1", filename="report.md")
        self.assertEqual(response.status_code, 404)
