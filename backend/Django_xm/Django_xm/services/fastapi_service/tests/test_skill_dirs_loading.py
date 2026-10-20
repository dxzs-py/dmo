"""SessionExecutor._load_skill_dirs 回归测试（运行时冒烟发现的 async 上下文 ORM bug）。

背景：_build_agent 曾在 async 上下文直调 SkillAdapter.to_deep_agent_skills()
（内部为同步 ORM 查询），抛 SynchronousOnlyOperation 后被 except 吞掉，
导致深度研究链路选中的 Skill 工具静默不加载。
修复：抽出自 _load_skill_dirs 同步方法，调用处经 sync_to_async 包装。
本测试用真实 DB 行锁定加载与过滤语义。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.services.fastapi_service.tests.test_skill_dirs_loading
"""

import os
import shutil
import tempfile
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase

from Django_xm.apps.tools.models import SkillPackage, ToolCategory
from Django_xm.services.fastapi_service.session_executor import SessionExecutor


def _make_executor(user_id: int) -> SessionExecutor:
    """构造真实 SessionExecutor（与既有测试同构）。"""
    return SessionExecutor(
        mock.Mock(),
        thread_id="t-skill",
        query="q",
        user_id=user_id,
        session_id="s-skill",
        message_id="m1",
        params={},
    )


class LoadSkillDirsTests(TestCase):
    """Skill 目录加载：active/属主/系统级/目录有效性过滤语义。

    注：依赖 Django TestCase 的每测试事务回滚，不做手动数据清理——
    手动删除用户会触发 delete_user_all_data 信号级联，得不偿失。
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = get_user_model().objects.create_user(
            username="skill_loader_a", password="x", email="skill_a@example.com"
        )
        cls.other = get_user_model().objects.create_user(
            username="skill_loader_b", password="x", email="skill_b@example.com"
        )
        cls.category = ToolCategory.objects.create(name="skill-test-category")

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="skill_pkg_")
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def _create_pkg(self, name, *, owner, source, status="active", skill_dir=None):
        return SkillPackage.objects.create(
            user=owner,
            name=name,
            description="demo",
            tool_type="skill",
            category=self.category,
            source=source,
            status=status,
            skill_dir=skill_dir if skill_dir is not None else self.tmp_dir,
        )

    def tearDown(self):
        SkillPackage.objects.filter(
            name__in=["pkg_a", "pkg_other", "pkg_disabled", "pkg_missing_dir"]
        ).delete()

    def test_returns_own_and_system_active_dirs(self):
        """属主 active 包 + 系统级 active 包均返回；他人包不串。"""
        self._create_pkg("pkg_a", owner=self.user, source="user")
        self._create_pkg("pkg_other", owner=self.other, source="user")
        self._create_pkg("pkg_a", owner=self.other, source="system")

        executor = _make_executor(self.user.id)
        dirs = executor._load_skill_dirs(["pkg_a"])

        self.assertEqual(dirs, [self.tmp_dir, self.tmp_dir])

    def test_excludes_disabled_and_missing_dir(self):
        """disabled 状态与 skill_dir 不存在的包被排除。"""
        self._create_pkg("pkg_a", owner=self.user, source="user", status="disabled")
        self._create_pkg("pkg_missing_dir", owner=self.user, source="user", skill_dir="Z:/no/such/dir")

        executor = _make_executor(self.user.id)
        dirs = executor._load_skill_dirs(["pkg_a", "pkg_missing_dir"])

        self.assertEqual(dirs, [])

    def test_empty_selection_returns_empty(self):
        """空选择返回空列表（不触发查询分支语义）。"""
        executor = _make_executor(self.user.id)
        self.assertEqual(executor._load_skill_dirs([]), [])
