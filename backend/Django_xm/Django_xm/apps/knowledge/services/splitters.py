"""
文本分块器模块

提供多种文本分块策略，用于将长文档分割成适合向量化的小块。

支持的分块器：
- RecursiveCharacterTextSplitter: 递归字符分块（推荐）
- CharacterTextSplitter: 简单字符分块
- MarkdownTextSplitter: Markdown 专用分块
- TokenTextSplitter: 基于 Token 的分块
- SemanticChunker: 语义分块（基于嵌入模型相似度）

元数据增强：
- chunk_index: 当前分块在文档中的序号
- total_chunks: 文档总分块数
- source_filename: 源文件名
- doc_type: 文档类型
- heading: Markdown 标题层级（h1/h2/h3）
- page_number: PDF 页码信息
"""

import re
from typing import Literal

from langchain_core.documents import Document
from langchain_text_splitters import (
    CharacterTextSplitter,
    MarkdownTextSplitter,
    RecursiveCharacterTextSplitter,
    TokenTextSplitter,
)

try:
    from langchain_text_splitters import (
        SemanticChunker,  # type: ignore[attr-defined]  # optional dep, may not exist in installed version
    )

    SEMANTIC_CHUNKER_AVAILABLE = True
except ImportError:
    SEMANTIC_CHUNKER_AVAILABLE = False

from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.apps.knowledge.config import settings

logger = get_logger(__name__)


SplitterType = Literal["recursive", "character", "markdown", "token", "semantic"]


def get_text_splitter(
    splitter_type: SplitterType = "recursive",
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    **kwargs,
):
    chunk_size: int = chunk_size or getattr(settings, "chunk_size", 1000)
    chunk_overlap: int = chunk_overlap or getattr(settings, "chunk_overlap", 200)

    logger.debug(f"创建文本分块器: type={splitter_type}, chunk_size={chunk_size}, chunk_overlap={chunk_overlap}")

    if splitter_type == "semantic":
        return _get_semantic_splitter(chunk_size, chunk_overlap, **kwargs)

    if splitter_type == "recursive":
        return RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            is_separator_regex=False,
            **kwargs,
        )
    elif splitter_type == "character":
        return CharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separator="\n\n",
            length_function=len,
            is_separator_regex=False,
            **kwargs,
        )
    elif splitter_type == "markdown":
        return MarkdownTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            **kwargs,
        )
    elif splitter_type == "token":
        return TokenTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            **kwargs,
        )
    else:
        raise ValueError(
            f"不支持的分块器类型: {splitter_type}。支持的类型: recursive, character, markdown, token, semantic"
        )


def _get_semantic_splitter(
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    **kwargs,
):
    if not SEMANTIC_CHUNKER_AVAILABLE:
        logger.warning(
            "SemanticChunker 不可用，回退到 RecursiveCharacterTextSplitter。"
            "请安装 langchain_text_splitters 以支持语义分块。"
        )
        return RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            is_separator_regex=False,
        )

    try:
        from Django_xm.apps.knowledge.services.embedding_service import get_embeddings

        embeddings = get_embeddings()
    except Exception as e:
        logger.warning(f"获取嵌入模型失败: {e}，回退到 RecursiveCharacterTextSplitter")
        return RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            is_separator_regex=False,
        )

    breakpoint_threshold_type = kwargs.pop("breakpoint_threshold_type", "percentile")
    breakpoint_threshold_amount = kwargs.pop("breakpoint_threshold_amount", 75)

    logger.debug(
        f"创建语义分块器: breakpoint_threshold_type={breakpoint_threshold_type}, "
        f"breakpoint_threshold_amount={breakpoint_threshold_amount}"
    )

    return SemanticChunker(
        embeddings=embeddings,
        breakpoint_threshold_type=breakpoint_threshold_type,
        breakpoint_threshold_amount=breakpoint_threshold_amount,
        **kwargs,
    )


def _extract_markdown_heading(text: str) -> str | None:
    """
    从 Markdown 文本中提取最近的标题层级（h1/h2/h3）

    返回文本中最后一个标题行，如 "# 引言" 或 "## 1.1 概述"
    """
    heading_pattern = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
    matches = list(heading_pattern.finditer(text))
    if matches:
        last_match = matches[-1]
        return last_match.group(0).strip()
    return None


def _determine_doc_type(doc: Document) -> str:
    """根据文档元数据和内容判断文档类型"""
    file_type = doc.metadata.get("file_type", "")
    if file_type in (".md", ".mdx"):
        return "markdown"
    if file_type == ".pdf":
        return "pdf"
    if file_type in (".html", ".htm"):
        return "html"
    if file_type in (".docx", ".doc"):
        return "docx"
    if file_type in (".txt",):
        return "text"
    if file_type in (".json",):
        return "json"
    if file_type in (".csv",):
        return "csv"
    # 根据 source 字段推断
    source = doc.metadata.get("source", "")
    if source:
        ext = "." + source.rsplit(".", 1)[-1].lower() if "." in source else ""
        ext_map = {
            ".md": "markdown",
            ".mdx": "markdown",
            ".pdf": "pdf",
            ".html": "html",
            ".htm": "html",
            ".txt": "text",
            ".docx": "docx",
            ".json": "json",
            ".csv": "csv",
        }
        if ext in ext_map:
            return ext_map[ext]
    return "unknown"


def enhance_chunk_metadata(chunks: list[Document]) -> list[Document]:
    """
    为切分后的文档块增强元数据

    添加字段：
    - chunk_index: 当前分块在源文档中的序号
    - total_chunks: 源文档总分块数
    - source_filename: 源文件名
    - doc_type: 文档类型
    - heading: Markdown 文档的最近标题层级
    - page_number: PDF 文档的页码（如果原始 loader 提供）
    """
    if not chunks:
        return chunks

    # 按源文件分组，同一源文件的分块共享 total_chunks
    source_groups: dict = {}
    for chunk in chunks:
        source_key = chunk.metadata.get("source", "") or chunk.metadata.get("file_name", "")
        if source_key not in source_groups:
            source_groups[source_key] = []
        source_groups[source_key].append(chunk)

    for source_key, group in source_groups.items():
        total_chunks = len(group)
        # 提取源文件名
        source_filename = ""
        if source_key:
            source_filename = source_key.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]

        for idx, chunk in enumerate(group):
            # 基础元数据
            chunk.metadata["chunk_index"] = idx
            chunk.metadata["total_chunks"] = total_chunks
            chunk.metadata["source_filename"] = source_filename or chunk.metadata.get("file_name", "")

            # 文档类型
            doc_type = _determine_doc_type(chunk)
            chunk.metadata["doc_type"] = doc_type

            # Markdown: 提取标题层级
            if doc_type == "markdown":
                heading = _extract_markdown_heading(chunk.page_content)
                if heading:
                    chunk.metadata["heading"] = heading

            # PDF: 保留 page_number（PyPDFLoader 自动添加 page 字段）
            if doc_type == "pdf" and "page" in chunk.metadata:
                chunk.metadata["page_number"] = chunk.metadata["page"] + 1

    return chunks


def split_documents(
    documents: list[Document],
    splitter_type: SplitterType = "recursive",
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    **kwargs,
) -> list[Document]:
    if not documents:
        logger.warning("文档列表为空，无需分块")
        return []

    logger.info(f"📝 开始分块: {len(documents)} 个文档")

    splitter = get_text_splitter(
        splitter_type=splitter_type,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        **kwargs,
    )

    try:
        chunks = splitter.split_documents(documents)

        # 切分后增强元数据
        chunks = enhance_chunk_metadata(chunks)

        logger.info(f"✅ 分块完成: {len(chunks)} 个文本块")

        total_chars = sum(len(chunk.page_content) for chunk in chunks)
        avg_chars = total_chars / len(chunks) if chunks else 0

        logger.info(f"   平均块大小: {avg_chars:.0f} 字符")
        logger.info(f"   总字符数: {total_chars}")

        return chunks

    except Exception:
        logger.exception("❌ 分块失败")
        raise


def split_text(
    text: str,
    splitter_type: SplitterType = "recursive",
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    metadata: dict | None = None,
    **kwargs,
) -> list[Document]:
    if not text:
        logger.warning("文本为空，无需分块")
        return []

    logger.info(f"📝 开始分块文本: {len(text)} 字符")

    splitter = get_text_splitter(
        splitter_type=splitter_type,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        **kwargs,
    )

    try:
        metadatas = [metadata] if metadata else None
        chunks = splitter.create_documents(
            texts=[text],
            metadatas=metadatas,
        )

        logger.info(f"✅ 分块完成: {len(chunks)} 个文本块")

        return chunks

    except Exception:
        logger.exception("❌ 分块失败")
        raise


def get_optimal_chunk_size(
    document_type: str = "general",
) -> tuple:
    recommendations = {
        "general": (1000, 200),
        "code": (1500, 300),
        "markdown": (800, 150),
        "academic": (1200, 250),
        "chat": (500, 50),
        "semantic": (0, 0),
    }

    if document_type not in recommendations:
        logger.warning(f"未知的文档类型: {document_type}，使用默认参数")
        return recommendations["general"]

    chunk_size, overlap = recommendations[document_type]
    logger.info(f"📊 推荐的分块参数 ({document_type}): chunk_size={chunk_size}, overlap={overlap}")

    return chunk_size, overlap


def analyze_chunks(chunks: list[Document]) -> dict:
    """分析分块结果的统计信息"""
    if not chunks:
        return {
            "total_chunks": 0,
            "total_chars": 0,
            "avg_chunk_size": 0,
            "min_chunk_size": 0,
            "max_chunk_size": 0,
        }

    chunk_sizes = [len(chunk.page_content) for chunk in chunks]
    total_chars = sum(chunk_sizes)

    stats = {
        "total_chunks": len(chunks),
        "total_chars": total_chars,
        "avg_chunk_size": total_chars / len(chunks),
        "min_chunk_size": min(chunk_sizes),
        "max_chunk_size": max(chunk_sizes),
    }

    logger.info("📊 分块统计:")
    logger.info(f"   总块数: {stats['total_chunks']}")
    logger.info(f"   总字符数: {stats['total_chars']}")
    logger.info(f"   平均大小: {stats['avg_chunk_size']:.0f} 字符")
    logger.info(f"   最小块: {stats['min_chunk_size']} 字符")
    logger.info(f"   最大块: {stats['max_chunk_size']} 字符")

    return stats
