"""审批访问控制 Mixin。

统一审批归属校验入口：
- 优先使用 Approval.user 外键直接过滤（性能更佳，无跨表 JOIN）
- user 字段为 NULL 的历史数据回退到三路关联校验（_user_owns_approval）

被 ApprovalDetailView / ApprovalResumeView / ApprovalRejectView / ApprovalStateView 复用。
"""

from django.apps import apps as _apps
from django.core.exceptions import PermissionDenied

from Django_xm.apps.approvals.models import Approval


class BaseApprovalAccessMixin:
    """审批归属校验 Mixin。

    使用方式::

        class ApprovalDetailView(BaseApprovalAccessMixin, APIView):
            def get(self, request, interrupt_id):
                approval = Approval.objects.get(interrupt_id=interrupt_id)
                self._assert_ownership(approval, request.user)
                # ...

    或直接通过 `_get_owned_approval` 查询并校验::

        approval = self._get_owned_approval(interrupt_id, request.user)
        self._assert_ownership(approval, request.user)
    """

    @staticmethod
    def _user_owns_approval(user, approval: Approval | None) -> bool:
        """检查用户是否拥有该审批。

        优先比较 ``approval.user_id``；为 NULL 时回退到关联校验：
        1. ``chat_session_id`` → ``ChatSession.session_id`` → ``ChatSession.user``
        2. ``source='deep_research'`` 时 ``source_id`` → ``ResearchTask.task_id`` → ``ResearchTask.created_by``
        """
        if approval is None:
            return False

        # 优先使用 user 字段（已回填或新建数据）
        if approval.user_id is not None:
            return approval.user_id == user.id

        # fallback：三路关联校验（历史数据 user 字段为 NULL）
        if approval.chat_session_id:
            ChatSession = _apps.get_model("chat", "ChatSession")
            if ChatSession.objects.filter(
                session_id=approval.chat_session_id,
                user=user,
                is_deleted=False,
            ).exists():
                return True

        if approval.source == Approval.Source.DEEP_RESEARCH:
            ResearchTask = _apps.get_model("research", "ResearchTask")
            if ResearchTask.objects.filter(
                task_id=approval.source_id,
                created_by=user,
                is_deleted=False,
            ).exists():
                return True

        return False

    @classmethod
    def _get_owned_approval(cls, interrupt_id: str, user) -> Approval | None:
        """查询单个 Approval，优先按 user 字段过滤。

        Returns:
            Approval | None: 命中且归属通过则返回模型实例；不存在或不归属则返回 None。
        """
        # 优先按 user 字段直接过滤
        approval = Approval.objects.filter(user=user, interrupt_id=interrupt_id).first()
        if approval is not None:
            return approval

        # fallback：user 字段为 NULL 的历史数据，按 interrupt_id 查询后调用 _user_owns_approval
        approval = Approval.objects.filter(interrupt_id=interrupt_id, user__isnull=True).first()
        if approval is not None and cls._user_owns_approval(user, approval):
            return approval
        return None

    @classmethod
    def _assert_ownership(cls, approval: Approval | None, user) -> None:
        """校验归属，不通过抛 PermissionDenied。

        Args:
            approval: Approval 模型实例（可能为 None）
            user: 当前请求用户

        Raises:
            PermissionDenied: 当 approval 为 None 或不属于该用户时。
        """
        if approval is None or not cls._user_owns_approval(user, approval):
            raise PermissionDenied("无权访问该审批")
