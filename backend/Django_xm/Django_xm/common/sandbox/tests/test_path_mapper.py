"""path_mapper 路径映射单测（沙箱加固：映射根 = DATA_DIR）。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.common.sandbox.tests.test_path_mapper --noinput
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.conf import settings
from django.test import SimpleTestCase

from Django_xm.common.sandbox.path_mapper import (
    CONTAINER_WORKSPACE,
    map_to_container_path,
)


class PathMapperTests(SimpleTestCase):
    """映射根切换为 DATA_DIR 后的路径映射/拒绝/回写行为。"""

    def setUp(self):
        self.data_dir = str(settings.DATA_DIR).rstrip("\\/")

    def test_data_dir_root_maps_to_workspace(self):
        """DATA_DIR 根 → /workspace（容器挂载点）。"""
        self.assertEqual(map_to_container_path(self.data_dir), CONTAINER_WORKSPACE)

    def test_research_dir_maps_to_workspace_research(self):
        """DATA_DIR/research/t1 → /workspace/research/t1（深研工作目录）。"""
        host = f"{self.data_dir}\\research\\t1"
        self.assertEqual(map_to_container_path(host), "/workspace/research/t1")

    def test_report_file_maps_deep(self):
        """DATA_DIR/research/t1/reports/x.md → /workspace/research/t1/reports/x.md。"""
        host = f"{self.data_dir}/research/t1/reports/x.md"
        self.assertEqual(
            map_to_container_path(host), "/workspace/research/t1/reports/x.md"
        )

    def test_outside_data_dir_rejected(self):
        """非 DATA_DIR 内路径（项目根 / 系统临时目录）→ None（拒绝映射）。"""
        project_root = str(settings.PROJECT_ROOT)
        self.assertIsNone(map_to_container_path(project_root))
        self.assertIsNone(map_to_container_path(project_root + "\\README.md"))

    def test_relative_path_joins_workspace(self):
        """相对路径直接拼接到 /workspace 下。"""
        self.assertEqual(map_to_container_path("research/t2"), "/workspace/research/t2")
        self.assertEqual(map_to_container_path(""), CONTAINER_WORKSPACE)
