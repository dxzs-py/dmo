"""
文档加载器模块
提供统一的文档加载接口，支持多种文档格式
"""

import os
from pathlib import Path
from typing import Any

from langchain_core.documents import Document

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)

SUPPORTED_EXTENSIONS = {
    ".pdf": "pdf",
    ".txt": "text",
    ".md": "markdown",
    ".mdx": "markdown",
    ".html": "html",
    ".htm": "html",
    ".json": "json",
    ".csv": "csv",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
}


def get_document_loader(file_path: str) -> Any | None:
    path = Path(file_path)
    extension = path.suffix.lower()

    if extension not in SUPPORTED_EXTENSIONS:
        logger.warning(f"不支持的文件类型: {extension}, 文件: {path}")
        return None

    file_type = SUPPORTED_EXTENSIONS[extension]

    try:
        if file_type == "pdf":
            from langchain_community.document_loaders import PyPDFLoader

            return PyPDFLoader(str(path))
        elif file_type == "text":
            from langchain_community.document_loaders import TextLoader

            return TextLoader(str(path), encoding="utf-8")
        elif file_type == "markdown":
            try:
                from langchain_unstructured import UnstructuredLoader

                return UnstructuredLoader(str(path), partition_strategy="fast")
            except ImportError:
                from langchain_community.document_loaders import UnstructuredMarkdownLoader

                return UnstructuredMarkdownLoader(str(path))
        elif file_type == "html":
            try:
                from langchain_unstructured import UnstructuredLoader

                return UnstructuredLoader(str(path), partition_strategy="fast")
            except ImportError:
                from langchain_community.document_loaders import UnstructuredHTMLLoader

                return UnstructuredHTMLLoader(str(path))
        elif file_type == "json":
            from langchain_community.document_loaders import JSONLoader

            return JSONLoader(file_path=str(path), jq_schema=".", text_content=False)
        elif file_type == "csv":
            from langchain_community.document_loaders import CSVLoader

            return CSVLoader(str(path), encoding="utf-8")
        elif file_type in ("docx", "xlsx", "pptx"):
            try:
                from langchain_unstructured import UnstructuredLoader

                return UnstructuredLoader(str(path), partition_strategy="fast")
            except ImportError:
                logger.warning(
                    f"Office 文件 {file_type} 需要 langchain-unstructured，请运行: pip install langchain-unstructured"
                )
                return None
        else:
            logger.warning(f"未实现的文件类型处理: {file_type}")
            return None
    except Exception:
        logger.exception(f"创建加载器失败: {path}, 错误")
        return None


def load_document(file_path: str, add_metadata: bool = True) -> list[Document]:
    """加载单个文档"""
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")

    if not path.is_file():
        raise ValueError(f"不是文件: {path}")

    loader = get_document_loader(str(path))
    if loader is None:
        extension = path.suffix.lower()
        supported = ", ".join(SUPPORTED_EXTENSIONS.keys())
        raise ValueError(f"不支持的文件类型: {extension}。支持的类型: {supported}")

    try:
        documents = loader.load()

        if add_metadata:
            file_type = path.suffix.lower()
            # 根据扩展名推断文档类型
            doc_type = SUPPORTED_EXTENSIONS.get(file_type, "unknown")
            for doc in documents:
                doc.metadata.update(
                    {
                        "source": str(path),
                        "file_name": path.name,
                        "file_type": file_type,
                        "doc_type": doc_type,
                    }
                )

        logger.info(f"文档加载成功: {path.name}, {len(documents)} 个文档块")
        return documents

    except Exception:
        logger.exception(f"文档加载失败: {path}, 错误")
        raise


def load_documents_from_directory(
    directory_path: str,
    recursive: bool = True,
    extensions: list[str] | None = None,
    add_metadata: bool = True,
) -> list[Document]:
    """从目录加载所有支持的文档"""
    dir_path = Path(directory_path)

    if not dir_path.exists():
        raise FileNotFoundError(f"目录不存在: {dir_path}")

    if not dir_path.is_dir():
        raise ValueError(f"不是目录: {dir_path}")

    all_documents: list[Document] = []
    loaded_count = 0
    failed_count = 0

    if extensions is None:
        extensions = list(SUPPORTED_EXTENSIONS.keys())

    extensions = [ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in extensions]

    logger.info(f"开始扫描目录: {directory_path}")

    try:
        for root, _dirs, files in os.walk(directory_path):
            if not recursive and root != str(dir_path):
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

    except Exception:
        logger.exception(f"目录加载失败: {directory_path}, 错误")
        raise
