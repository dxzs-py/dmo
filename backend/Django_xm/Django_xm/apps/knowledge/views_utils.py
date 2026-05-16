import os
from .models import DocumentFileType


def get_file_extension(filename):
    return os.path.splitext(filename)[1].lower().lstrip('.')


def get_document_type(extension):
    type_map = {
        'pdf': DocumentFileType.PDF,
        'txt': DocumentFileType.TXT,
        'md': DocumentFileType.MD,
        'csv': DocumentFileType.CSV,
        'docx': DocumentFileType.DOCX,
        'xlsx': DocumentFileType.XLSX,
        'html': DocumentFileType.HTML,
        'json': DocumentFileType.JSON,
    }
    return type_map.get(extension, DocumentFileType.OTHER)


def get_user_index_name(user, index_name):
    return f"user_{user.id}_{index_name}"


def get_original_index_name(user_index_name):
    parts = user_index_name.split('_', 2)
    if len(parts) >= 3 and parts[0] == 'user':
        return parts[2]
    return user_index_name
