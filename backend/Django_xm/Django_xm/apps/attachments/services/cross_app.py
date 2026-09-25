"""
Attachments 跨 app 服务层 - 供其他 app 调用的接口

解耦其他 app 对 attachments.models 的直接导入，通过薄封装的 ORM 查询提供服务。
"""

from django.apps import apps
from django.utils import timezone

from ..models import AttachmentStatus


def get_attachment_by_id(attachment_id):
    """获取附件对象（含 session 预取），不存在返回 None"""
    ChatAttachment = apps.get_model("attachments", "ChatAttachment")
    return ChatAttachment.objects.select_related("session").filter(id=attachment_id).first()


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
