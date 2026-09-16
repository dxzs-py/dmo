"""
抽象基类模型
提供软删除、审计字段等通用功能
所有业务模型应继承此类
"""

import threading

from django.conf import settings
from django.db import models

# ──────────────────────────────────────────────
# 线程局部存储：用于在同一线程内跨函数传递当前 HTTP 请求对象
# Django 每个请求由独立线程处理，threading.local() 保证
# 不同线程之间互不干扰，避免并发请求时用户信息串线
# ──────────────────────────────────────────────
_thread_locals = threading.local()  # 创建的是一个空容器对象，只是一个"隔离访问器"。


def set_current_request(request):
    """在中间件中调用，将当前请求绑定到线程局部变量"""
    _thread_locals.request = request


def get_current_request():
    """获取当前线程绑定的请求对象，未设置时返回 None"""
    return getattr(_thread_locals, "request", None)


def clear_current_request():
    """请求结束后在中间件中调用，清理线程局部变量防止内存泄漏"""
    if hasattr(_thread_locals, "request"):
        del _thread_locals.request

"""
为什么是全局变量：必须全局唯一，这样所有线程访问的是同一个容器对象，而 threading.local() 内部机制保证每个线程读写时自动隔离到各自的命名空间。
上面这些代码的效果：
_thread_locals（全局唯一对象）
  │
  └── 内部字典（简化示意）：
      {
        线程A_id: { "request": req_A },    ← 线程A 读写这里
        线程B_id: { "request": req_B },    ← 线程B 读写这里
        线程C_id: { },                     ← 线程C 还没 set，为空
      }
      
"""

class SoftDeleteManager(models.Manager):
    """默认管理器：查询时自动过滤掉已软删除的记录（is_deleted=True）

    使用方式：Model.objects.all()  → 只返回未删除的记录
    """

    def get_queryset(self):
        # 在原始 queryset 基础上追加 is_deleted=False 过滤条件
        return super().get_queryset().filter(is_deleted=False)


class AllObjectsManager(models.Manager):
    """全量管理器：查询时包含所有记录（含已软删除的）

    使用方式：Model.all_objects.all()  → 返回全部记录，包括已删除的
    """

    def get_queryset(self):
        # 不追加任何过滤，返回完整数据集
        return super().get_queryset()


class BaseModel(models.Model):
    """基础抽象模型：提供软删除 + 时间戳字段

    继承此类的模型自动拥有：
    - created_at / updated_at：自动维护的创建/更新时间
    - is_deleted / deleted_at：软删除标记与删除时间
    - objects：默认只查未删除记录
    - all_objects：可查全部记录（含已删除）
    """

    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    is_deleted = models.BooleanField(default=False, db_index=True, verbose_name="是否已删除")
    deleted_at = models.DateTimeField(null=True, blank=True, verbose_name="删除时间")

    # 默认管理器只返回未删除记录；all_objects 返回全部记录
    objects = SoftDeleteManager()  # 如果不写Django 自动添加：objects = models.Manager()，是替换默认的 objects 管理器
    all_objects = AllObjectsManager()  # 额外新增一个管理器

    class Meta:
        abstract = True  # 声明为抽象模型，不会生成独立的数据库表

    def soft_delete(self, using=None):
        """软删除：将 is_deleted 标记为 True 并记录删除时间，数据仍保留在数据库中

        Args:
            using: 指定数据库别名（多数据库场景），默认 None 使用默认库
        """
        self.is_deleted = True
        from django.utils import timezone

        self.deleted_at = timezone.now()
        self.save(using=using)

    def restore(self, using=None):
        """恢复软删除：将 is_deleted 重置为 False，清空删除时间"""
        self.is_deleted = False
        self.deleted_at = None
        self.save(using=using)

    def hard_delete(self, using=None):
        """硬删除：真正从数据库中删除记录（不可恢复）"""
        super().delete(using=using)


class AuditModel(BaseModel):
    """审计模型：在 BaseModel 基础上增加创建人/更新人字段

    重写 save() 方法，自动从请求上下文中获取当前登录用户，
    新建时设置 created_by，每次保存时更新 updated_by。

    使用前提：需要在 Django 中间件中调用 set_current_request() /
    clear_current_request() 将请求对象绑定到线程局部变量。
    """

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,  # 关联用户被删除时，本字段置 NULL 而非级联删除
        null=True,
        blank=True,
        related_name="created_%(class)ss",  # 动态反向关联名，如 User.created_orders
        verbose_name="创建人",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_%(class)ss",  # 动态反向关联名，如 User.updated_orders
        verbose_name="更新人",
    )

    class Meta(BaseModel.Meta):
        abstract = True  # 同样是抽象模型，不会生成独立表

    def save(self, *args, **kwargs):
        """重写 save：自动填充 created_by / updated_by

        获取请求对象的优先级：
        1. 调用方通过 save(request=req) 显式传入
        2. 从线程局部变量 get_current_request() 获取（中间件注入）

        如果都没有且是新建对象，会记录 warning 日志（CeleryTaskRecord 除外，
        因为 Celery 任务本身就没有 HTTP 请求上下文，属于正常情况）。
        """
        # 尝试从 kwargs 中弹出 request 参数（显式传入优先）
        request = kwargs.pop("request", None)
        if request is None:
            # 回退到线程局部变量中获取（由中间件 set_current_request 注入）
            request = get_current_request()

        if request and hasattr(request, "user") and request.user and request.user.is_authenticated:
            # 有已认证用户：新建时设置 created_by，每次保存都更新 updated_by
            if not self.pk:
                # self.pk（主键） 为 None 说明是新建对象（尚未持久化到数据库）
                self.created_by = request.user
            self.updated_by = request.user
        elif not self.pk and not self.created_by:
            # 无请求上下文 + 新建对象 + created_by 未手动设置 → 记录警告
            # CeleryTaskRecord 是例外：异步任务天然没有 HTTP 请求，不应告警
            from Django_xm.apps.core.task_models import CeleryTaskRecord

            if not isinstance(self, CeleryTaskRecord):
                import logging

                logging.getLogger(__name__).warning(
                    f"AuditModel.save() called without request context: "
                    f"model={self.__class__.__name__}, created_by will be None"
                )
        super().save(*args, **kwargs)