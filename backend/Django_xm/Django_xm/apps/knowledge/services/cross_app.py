"""
Knowledge 跨 app 服务层 - 供其他 app 调用的接口

解耦其他 app 对 knowledge.models 的直接导入，通过薄封装的 ORM 查询提供服务。
"""

from django.apps import apps


def get_user_document_count(user):
    """获取用户的文档数量"""
    Document = apps.get_model("knowledge", "Document")
    return Document.objects.filter(index__user=user, is_deleted=False).count()


def get_user_index_count(user):
    """获取用户的知识库索引数量"""
    DocumentIndex = apps.get_model("knowledge", "DocumentIndex")
    return DocumentIndex.objects.filter(user=user, is_deleted=False).count()


def get_index_manager():
    """供其他应用调用：获取索引管理器实例"""
    from .index_service import IndexManager

    return IndexManager()
