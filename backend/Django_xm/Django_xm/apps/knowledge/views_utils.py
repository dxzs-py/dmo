import os

from .models import DocumentFileType


def get_file_extension(filename):
    return os.path.splitext(filename)[1].lower().lstrip(".")


def get_document_type(extension):
    type_map = {
        "pdf": DocumentFileType.PDF,
        "txt": DocumentFileType.TXT,
        "md": DocumentFileType.MD,
        "csv": DocumentFileType.CSV,
        "docx": DocumentFileType.DOCX,
        "xlsx": DocumentFileType.XLSX,
        "html": DocumentFileType.HTML,
        "json": DocumentFileType.JSON,
    }
    return type_map.get(extension, DocumentFileType.OTHER)


def build_user_index_name(user_id, index_name):
    """知识库索引全名规则单源（D-1，多租户隔离主机制）。

    规则：``user_{user_id}_{index_name}``。所有按 user 隔离的索引名必须经本
    函数拼接，禁止各处内联 f-string 副本（原 rag_chat_service/_get_user_index_name、
    multi_kb_retriever 内联、chat_service 内联、learning retrieval_node 等价
    构造均已收敛到此）。

    Args:
        user_id: 用户 ID（int）；调用方需自行保证非空——本函数不做回退语义
            （原 rag_chat_service 副本的"user_id 为空返回原始名"分支为不可达
            死代码，已随收敛显式删除）。
        index_name: 原始知识库名（kb_id）
    """
    return f"user_{user_id}_{index_name}"


def get_user_index_name(user, index_name):
    """user 对象版便利封装（委托 build_user_index_name 单源规则）。"""
    return build_user_index_name(user.id, index_name)


def get_original_index_name(user_index_name):
    parts = user_index_name.split("_", 2)
    if len(parts) >= 3 and parts[0] == "user":
        return parts[2]
    return user_index_name
