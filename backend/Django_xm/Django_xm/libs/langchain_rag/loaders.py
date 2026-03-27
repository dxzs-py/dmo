"""
文档加载器模块

提供统一的文档加载接口，支持多种文档格式：
- PDF (.pdf)
- Markdown (.md, .mdx)
- 文本文件 (.txt)
- HTML (.html, .htm)
- JSON (.json)

使用 LangChain 的 Document Loaders API。

参考：
- https://reference.langchain.com/python/langchain_core/document_loaders/
- https://reference.langchain.com/python/langchain_community/document_loaders/
"""

import os
from pathlib import Path
from typing import List, Optional, Dict, Any
from langchain_core.documents import Document
from langchain_community.document_loaders import (
    PyPDFLoader,
    TextLoader,
    UnstructuredMarkdownLoader,
    UnstructuredHTMLLoader,
    JSONLoader,
    DirectoryLoader,
)

from ..langchain_core.config import get_logger

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
    """
    获取支持的文件扩展名
    """
    return SUPPORTED_EXTENSIONS.copy()


def get_document_loader(file_path: str) -> Optional[Any]:
    """
    根据文件类型获取合适的文档加载器
    """
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
            return JSONLoader(
                file_path=str(file_path),
                jq_schema=".",
                text_content=False,
            )
        else:
            logger.warning(f"未实现的文件类型处理: {file_type}")
            return None

    except Exception as e:
        logger.error(f"创建加载器失败: {file_path}, 错误: {e}")
        return None


def load_document(
    file_path: str,
    add_metadata: bool = True,
) -> List[Document]:
    """
    加载单个文档
    """
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    if not file_path.is_file():
        raise ValueError(f"不是文件: {file_path}")

    loader = get_document_loader(str(file_path))
    if loader is None:
        extension = file_path.suffix.lower()
        supported = ", ".join(SUPPORTED_EXTENSIONS.keys())
        raise ValueError(
            f"不支持的文件类型: {extension}。支持的类型: {supported}"
        )

    try:
        documents = loader.load()

        if add_metadata:
            for doc in documents:
                doc.metadata.update({
                    "source": str(file_path),
                    "file_name": file_path.name,
                    "file_type": file_path.suffix.lower(),
                })

        logger.info(f"✅ 文档加载成功: {file_path.name}, {len(documents)} 个文档块")
        return documents

    except Exception as e:
        logger.error(f"❌ 文档加载失败: {file_path}, 错误: {e}")
        raise


def load_documents_from_directory(
    directory_path: str,
    recursive: bool = True,
    extensions: Optional[List[str]] = None,
    add_metadata: bool = True,
) -> List[Document]:
    """
    从目录加载所有支持的文档
    """
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

    logger.info(f"📂 开始扫描目录: {directory_path}")

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
                    logger.warning(f"⚠️ 加载失败: {file_path}, 错误: {e}")
                    failed_count += 1

        logger.info(
            f"✅ 目录加载完成: {loaded_count} 个文件成功, {failed_count} 个失败, "
            f"共 {len(all_documents)} 个文档块"
        )

        return all_documents

    except Exception as e:
        logger.error(f"❌ 目录加载失败: {directory_path}, 错误: {e}")
        raise


def get_file_info(file_path: str) -> Dict[str, Any]:
    """
    获取文件信息
    """
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    stat = file_path.stat()

    return {
        "name": file_path.name,
        "path": str(file_path.absolute()),
        "extension": file_path.suffix.lower(),
        "size": stat.st_size,
        "modified_time": stat.st_mtime,
        "is_supported": file_path.suffix.lower() in SUPPORTED_EXTENSIONS,
    }
