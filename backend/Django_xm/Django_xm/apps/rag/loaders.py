"""
文档加载器模块
提供统一的文档加载接口，支持多种文档格式
"""

import os
from pathlib import Path
from typing import List, Optional, Dict, Any

from langchain_core.documents import Document
from langchain_community.document_loaders import (
    PyPDFLoader, TextLoader, UnstructuredMarkdownLoader,
    UnstructuredHTMLLoader, JSONLoader,
)

from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)

SUPPORTED_EXTENSIONS = {
    ".pdf": "pdf",
    ".txt": "text",
    ".md": "markdown",
    ".mdx": "markdown",
    ".html": "html",
    ".htm": "html",
    ".json": "json",
}


def get_supported_extensions() -> Dict[str, str]:
    """获取支持的文件扩展名"""
    return SUPPORTED_EXTENSIONS.copy()


def get_document_loader(file_path: str) -> Optional[Any]:
    """根据文件类型获取合适的文档加载器"""
    file_path = Path(file_path)
    extension = file_path.suffix.lower()

    if extension not in SUPPORTED_EXTENSIONS:
        logger.warning(f"不支持的文件类型: {extension}, 文件: {file_path}")
        return None

    file_type = SUPPORTED_EXTENSIONS[extension]

    try:
        if file_type == "pdf":
            return PyPDFLoader(str(file_path))
        elif file_type == "text":
            return TextLoader(str(file_path), encoding="utf-8")
        elif file_type == "markdown":
            return UnstructuredMarkdownLoader(str(file_path))
        elif file_type == "html":
            return UnstructuredHTMLLoader(str(file_path))
        elif file_type == "json":
            return JSONLoader(file_path=str(file_path), jq_schema=".", text_content=False)
        else:
            logger.warning(f"未实现的文件类型处理: {file_type}")
            return None
    except Exception as e:
        logger.error(f"创建加载器失败: {file_path}, 错误: {e}")
        return None


def load_document(file_path: str, add_metadata: bool = True) -> List[Document]:
    """加载单个文档"""
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    if not file_path.is_file():
        raise ValueError(f"不是文件: {file_path}")

    loader = get_document_loader(str(file_path))
    if loader is None:
        extension = file_path.suffix.lower()
        supported = ", ".join(SUPPORTED_EXTENSIONS.keys())
        raise ValueError(f"不支持的文件类型: {extension}。支持的类型: {supported}")

    try:
        documents = loader.load()

        if add_metadata:
            for doc in documents:
                doc.metadata.update({
                    "source": str(file_path),
                    "file_name": file_path.name,
                    "file_type": file_path.suffix.lower(),
                })

        logger.info(f"文档加载成功: {file_path.name}, {len(documents)} 个文档块")
        return documents

    except Exception as e:
        logger.error(f"文档加载失败: {file_path}, 错误: {e}")
        raise


def load_documents_from_directory(
    directory_path: str,
    recursive: bool = True,
    extensions: Optional[List[str]] = None,
    add_metadata: bool = True,
) -> List[Document]:
    """从目录加载所有支持的文档"""
    directory_path = Path(directory_path)

    if not directory_path.exists():
        raise FileNotFoundError(f"目录不存在: {directory_path}")

    if not directory_path.is_dir():
        raise ValueError(f"不是目录: {directory_path}")

    all_documents: List[Document] = []
    loaded_count = 0
    failed_count = 0

    if extensions is None:
        extensions = list(SUPPORTED_EXTENSIONS.keys())

    extensions = [ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in extensions]

    logger.info(f"开始扫描目录: {directory_path}")

    try:
        for root, dirs, files in os.walk(directory_path):
            if not recursive and root != str(directory_path):
                break

            for file_name in files:
                file_path = Path(root) / file_name
                extension = file_path.suffix.lower()

                if extension not in extensions:
                    continue

                try:
                    documents = load_document(str(file_path), add_metadata=add_metadata)
                    all_documents.extend(documents)
                    loaded_count += 1
                except Exception as e:
                    logger.warning(f"加载失败: {file_path}, 错误: {e}")
                    failed_count += 1

        logger.info(f"目录加载完成: {loaded_count} 个文件成功, {failed_count} 个失败, 共 {len(all_documents)} 个文档块")

        return all_documents

    except Exception as e:
        logger.error(f"目录加载失败: {directory_path}, 错误: {e}")
        raise


def load_documents_from_paths(
    file_paths: List[str],
    show_progress: bool = True,
) -> List[Document]:
    """从文件路径列表加载文档"""
    logger.info(f"📚 开始加载 {len(file_paths)} 个文件")

    all_documents: List[Document] = []
    success_count = 0
    error_count = 0

    for i, file_path in enumerate(file_paths, 1):
        try:
            if show_progress:
                logger.info(f"   [{i}/{len(file_paths)}] 加载: {Path(file_path).name}")

            documents = load_document(file_path, add_metadata=True)
            all_documents.extend(documents)
            success_count += 1

        except Exception as e:
            logger.error(f"   ❌ 加载失败: {file_path}, 错误: {e}")
            error_count += 1
            continue

    logger.info(f"✅ 批量加载完成:")
    logger.info(f"   成功: {success_count} 个文件")
    logger.info(f"   失败: {error_count} 个文件")
    logger.info(f"   总计: {len(all_documents)} 个文档块")

    return all_documents


def load_directory(
    directory_path: str,
    glob_pattern: str = "**/*",
    exclude_patterns: Optional[List[str]] = None,
    recursive: bool = True,
    show_progress: bool = True,
    max_files: Optional[int] = None,
) -> List[Document]:
    """
    批量加载目录中的文档（兼容源项目API）
    
    Args:
        directory_path: 目录路径
        glob_pattern: 文件匹配模式
        exclude_patterns: 排除的文件模式列表
        recursive: 是否递归加载子目录
        show_progress: 是否显示加载进度
        max_files: 最大加载文件数
        
    Returns:
        Document 对象列表
    """
    return load_documents_from_directory(
        directory_path=directory_path,
        recursive=recursive,
        add_metadata=True,
    )