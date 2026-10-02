"""
Attachments 跨 app 服务层 - 供其他 app 调用的接口

解耦其他 app 对 attachments.models 的直接导入，通过薄封装的 ORM 查询提供服务。
"""

from django.apps import apps
from django.utils import timezone

from ..models import AttachmentStatus


def get_attachment_by_id(attachment_id, user=None):
    """获取附件对象（含 session 预取），不存在返回 None。

    user 非 None 时强制属主过滤（session__user，S-09 IDOR 防护），
    跨用户附件直接查询不到（等价 404）；
    user=None 仅供系统内部调用（attachment_id 来自服务端查询而非 LLM 参数）。
    """
    ChatAttachment = apps.get_model("attachments", "ChatAttachment")
    queryset = ChatAttachment.objects.select_related("session").filter(id=attachment_id)
    if user is not None:
        queryset = queryset.filter(session__user=user)
    return queryset.first()


def soft_delete_session_attachments(session):
    """软删除会话的所有附件"""
    ChatAttachment = apps.get_model("attachments", "ChatAttachment")
    ChatAttachment.objects.filter(session=session, is_deleted=False).update(
        is_deleted=True, deleted_at=timezone.now(), status=AttachmentStatus.DELETED
    )


def get_attachment_service():
    """供其他应用调用：获取附件服务实例"""
    from .attachment_content_service import AttachmentService

    return AttachmentService()
