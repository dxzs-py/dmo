"""pytest 共享 fixture 与配置（兼容 pytest-django 未安装场景）。

提供：
- pytest marker 注册：unit / integration / slow（配合 --strict-markers）
- Django 环境自动初始化（不依赖 pytest-django，conftest 加载时即完成 django.setup）
- django_db_setup：当 pytest-django 可用时，会话级事务隔离测试数据库
- user_factory / session_factory / approval_factory：测试数据工厂
- redis_client：测试用 Redis 客户端（db=15，不可用时自动 skip）

设计说明：
- 现有测试基于 Django ``TestCase`` / ``unittest.TestCase``，无需 pytest-django 即可运行。
- 当 pytest-django 安装后，``django_db_setup`` 与 ``@pytest.mark.django_db`` 自动生效，
  支持纯 pytest 函数式测试。
- 工厂 fixture 不依赖 ``db`` fixture，兼容 TestCase（DB 由测试类管理）与
  ``@pytest.mark.django_db``（DB 由 pytest-django 管理）两种模式。

运行方式：
    cd backend/Django_xm
    conda activate langchain_xm
    python -m pytest                            # 全量
    python -m pytest -m unit                    # 仅单元测试
    python -m pytest -m "not slow"              # 跳过慢测试
    python -m pytest -m integration             # 仅集成测试
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from typing import Any

# ── Django 环境初始化（必须在导入 Django 模型之前完成）──────────
# 优先使用 test settings；允许通过环境变量覆盖（如需对比 dev 行为时）。
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")

import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

import pytest

# ── pytest-django 可用性检测 ──────────────────────────────────────
try:
    import pytest_django  # pylint: disable=unused-import  # noqa: F401

    HAS_PYTEST_DJANGO = True
except ImportError:
    HAS_PYTEST_DJANGO = False


# ============================================================================
# Marker 注册（配合 --strict-markers，避免未注册 marker 报错）
# ============================================================================
def pytest_configure(config: pytest.Config) -> None:
    """注册自定义 marker。

    在 pyproject.toml 的 markers 中也声明了同名 marker，此处重复注册保证
    即使 pyproject.toml 未加载（如直接指定 -c 指向其他配置）也能识别。
    """
    config.addinivalue_line("markers", "unit: 单元测试（不依赖外部服务）")
    config.addinivalue_line("markers", "integration: 集成测试（依赖 DB/Redis 等外部服务）")
    config.addinivalue_line("markers", "slow: 慢测试（执行时间 > 1s）")
    config.addinivalue_line("markers", "requires_redis: 依赖真实 Redis 实例（db=15），不可用时自动 skip")
    config.addinivalue_line("markers", "requires_celery: 依赖 Celery worker 运行，EAGER 模式下可降级")


# ============================================================================
# django_db_setup（仅 pytest-django 可用时生效）
# ============================================================================
if HAS_PYTEST_DJANGO:

    @pytest.fixture(scope="session")
    def django_db_setup(
        django_db_blocker: Any,
    ) -> Iterator[None]:
        """会话级测试数据库初始化（事务隔离策略）。

        - 创建 ``test_<dbname>`` 数据库并应用迁移，整个测试会话复用
        - 每个测试通过 ``@pytest.mark.django_db(transaction=True)`` 或
          ``transactional_db`` fixture 获得事务级隔离（每条测试后回滚）
        - 会话结束后销毁测试数据库

        注意：本 fixture 仅在 pytest-django 已安装时生效。未安装时，
        由下方 ``_setup_test_database`` autouse fixture 接管 DB 初始化。
        """
        from django.test.utils import setup_databases, teardown_databases

        with django_db_blocker.unblock():
            old_config = setup_databases(verbosity=0, interactive=False)
        yield
        with django_db_blocker.unblock():
            teardown_databases(old_config, verbosity=0)


# ============================================================================
# 测试数据库自动初始化（pytest-django 未安装时的兼容方案）
# ============================================================================
@pytest.fixture(scope="session", autouse=True)
def _setup_test_database() -> Iterator[None]:
    """会话级测试数据库自动初始化。

    pytest 原生不创建测试数据库（Django ``TestCase`` 依赖测试运行器完成）。
    - pytest-django 已安装：由 ``django_db_setup`` fixture 接管，此处直接 yield
    - pytest-django 未安装：调用 ``setup_databases`` 创建 schema，
      会话结束后 ``teardown_databases`` 清理

    迁移禁用策略：
        历史迁移中存在 PG 专用 SQL（如
        ``ALTER TABLE ... ADD CONSTRAINT ... UNIQUE``），
        在 SQLite 测试库上无法执行。本 fixture 通过设置
        ``MIGRATION_MODULES = {label: None}`` 禁用所有应用迁移，
        改为从当前 models 直接创建表 schema（等同 ``--no-migrations``）。
        这保证测试 schema 与当前代码一致，不受历史迁移 SQL 限制。

    Django ``TestCase`` 的 per-test 事务隔离（``_enter_atomics``/``_rollback_atomics``）
    由 Django 测试框架自动管理，本 fixture 仅负责 schema 创建。
    """
    if HAS_PYTEST_DJANGO:
        yield
        return

    from django.apps import apps as django_apps
    from django.conf import settings
    from django.test.utils import setup_databases, teardown_databases

    # 禁用所有应用迁移：从当前 models 创建 schema，跳过 PG 专用迁移 SQL
    settings.MIGRATION_MODULES = {config.label: None for config in django_apps.get_app_configs()}

    old_config = setup_databases(verbosity=0, interactive=False)
    yield
    teardown_databases(old_config, verbosity=0)


# ============================================================================
# 数据工厂 fixture
# ============================================================================
@pytest.fixture
def user_factory() -> Callable[..., Any]:
    """返回创建测试用户的工厂函数。

    使用前需确保 DB 可用：
    - Django ``TestCase`` 子类自动管理 DB 事务
    - pytest 函数式测试需标记 ``@pytest.mark.django_db``

    Returns:
        工厂函数，接受与 ``User.objects.create_user`` 一致的关键字参数，
        返回创建的 ``User`` 实例。``username`` / ``password`` 有默认值。
    """
    from django.contrib.auth import get_user_model

    User = get_user_model()
    counter = [0]

    def _create(**kwargs: Any) -> Any:
        counter[0] += 1
        defaults: dict[str, Any] = {
            "username": f"testuser_{counter[0]}",
            "password": "testpass123",
        }
        defaults.update(kwargs)
        return User.objects.create_user(**defaults)

    return _create


@pytest.fixture
def session_factory() -> Callable[..., Any]:
    """返回创建测试 ``ChatSession`` 的工厂函数。

    使用前需确保 DB 可用（同 ``user_factory``）。
    若未显式传入 ``user``，会自动创建一个测试用户作为会话归属。

    Returns:
        工厂函数，返回创建的 ``ChatSession`` 实例。
        ``session_id`` 未传入时自动生成 UUID。
    """
    from django.contrib.auth import get_user_model

    from Django_xm.apps.chat.models import ChatSession

    User = get_user_model()
    counter = [0]

    def _create(**kwargs: Any) -> Any:
        counter[0] += 1
        if "user" not in kwargs:
            kwargs["user"] = User.objects.create_user(
                username=f"session_user_{counter[0]}",
                password="testpass123",
            )
        defaults: dict[str, Any] = {
            "session_id": f"test-session-{uuid.uuid4().hex[:12]}",
            "title": f"测试会话 {counter[0]}",
            "mode": "agent",
        }
        defaults.update(kwargs)
        return ChatSession.objects.create(**defaults)

    return _create


@pytest.fixture
def approval_factory() -> Callable[..., Any]:
    """返回创建测试 ``Approval`` 的工厂函数。

    使用前需确保 DB 可用（同 ``user_factory``）。
    ``interrupt_id`` 未传入时自动生成唯一值。

    Returns:
        工厂函数，返回创建的 ``Approval`` 实例。
    """
    from Django_xm.apps.approvals.models import Approval

    counter = [0]

    def _create(**kwargs: Any) -> Any:
        counter[0] += 1
        defaults: dict[str, Any] = {
            "interrupt_id": f"test-interrupt-{counter[0]}-{uuid.uuid4().hex[:8]}",
            "source": Approval.SOURCE_CHAT,
            "source_id": f"test-source-{counter[0]}",
            "tool_name": "shell_exec",
            "title": f"测试审批 {counter[0]}",
            "operation": "ls -la",
            "danger_level": "medium",
            "state": Approval.STATE_PENDING,
        }
        defaults.update(kwargs)
        return Approval.objects.create(**defaults)

    return _create


# ============================================================================
# Redis 客户端 fixture（db=15，独立于开发/生产 db=1/2/3/4）
# ============================================================================
@pytest.fixture
def redis_client() -> Iterator[Any]:
    """测试用 Redis 客户端（db=15）。

    - 使用独立 db=15 避免与开发（db=1/2）/ Celery（db=3/4）数据冲突
    - Redis 不可用时自动 ``skip`` 测试，不报错
    - fixture 结束时 ``flushdb`` 清理 db=15，确保测试间隔离

    Yields:
        redis.Redis 客户端实例（已连接 db=15）。
    """
    try:
        import redis
    except ImportError:
        pytest.skip("redis 包未安装，跳过依赖 Redis 的测试")

    redis_url = os.environ.get("REDIS_TEST_URL", "redis://127.0.0.1:6379/15")
    try:
        client = redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
        client.ping()
    except Exception as e:
        pytest.skip(f"Redis 不可用（{redis_url}），跳过依赖 Redis 的测试: {e}")

    yield client

    # 清理 db=15，避免测试数据残留
    try:
        client.flushdb()
    except Exception:  # noqa: S110
        pass
    try:
        client.close()
    except Exception:  # noqa: S110
        pass


# ============================================================================
# mock_llm fixture：模拟 BaseChatModel，供 ai_engine 测试复用
# ============================================================================
@pytest.fixture
def mock_llm() -> Any:
    """返回模拟的 BaseChatModel 实例工厂函数。

    提供两种用法：
    1. ``mock_llm()`` 直接返回一个默认 mock model
    2. ``mock_llm(name="gpt-4", provider_id="openai")`` 自定义参数

    与 ``Django_xm/apps/ai_engine/tests/test_llm_factory.py`` 的
    ``make_mock_model`` 实现一致，提取为共享 fixture 消除重复。

    Returns:
        工厂函数，调用后返回 MagicMock 模拟的 chat model，
        携带 ``_llm_type`` 与 ``_provider_id`` 属性便于断言。
    """
    from unittest.mock import MagicMock

    def _create(name: str = "mock-model", provider_id: str = "mock") -> Any:
        model = MagicMock(name=name)
        model._llm_type = name
        model._provider_id = provider_id
        return model

    return _create
