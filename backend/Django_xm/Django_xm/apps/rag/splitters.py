"""
文本分块器模块

提供多种文本分块策略，用于将长文档分割成适合向量化的小块。

支持的分块器：
- RecursiveCharacterTextSplitter: 递归字符分块（推荐）
- CharacterTextSplitter: 简单字符分块
- MarkdownTextSplitter: Markdown 专用分块
- TokenTextSplitter: 基于 Token 的分块
"""

from typing import List, Optional, Literal
from langchain_core.documents import Document
from langchain_text_splitters import (
    RecursiveCharacterTextSplitter,
    CharacterTextSplitter,
    MarkdownTextSplitter,
    TokenTextSplitter,
)

from Django_xm.apps.core.config import settings, get_logger

logger = get_logger(__name__)


SplitterType = Literal["recursive", "character", "markdown", "token"]


def get_text_splitter(
    splitter_type: SplitterType = "recursive",
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
    **kwargs,
):
    chunk_size = chunk_size or getattr(settings, 'chunk_size', 1000)
    chunk_overlap = chunk_overlap or getattr(settings, 'chunk_overlap', 200)

    logger.debug(
        f"创建文本分块器: type={splitter_type}, "
        f"chunk_size={chunk_size}, chunk_overlap={chunk_overlap}"
    )

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
            f"不支持的分块器类型: {splitter_type}。"
            f"支持的类型: recursive, character, markdown, token"
        )


def split_documents(
    documents: List[Document],
    splitter_type: SplitterType = "recursive",
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
    **kwargs,
) -> List[Document]:
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

        logger.info(f"✅ 分块完成: {len(chunks)} 个文本块")

        total_chars = sum(len(chunk.page_content) for chunk in chunks)
        avg_chars = total_chars / len(chunks) if chunks else 0

        logger.info(f"   平均块大小: {avg_chars:.0f} 字符")
        logger.info(f"   总字符数: {total_chars}")

        return chunks

    except Exception as e:
        logger.error(f"❌ 分块失败: {e}")
        raise


def split_text(
    text: str,
    splitter_type: SplitterType = "recursive",
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
    metadata: Optional[dict] = None,
    **kwargs,
) -> List[Document]:
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

    except Exception as e:
        logger.error(f"❌ 分块失败: {e}")
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
    }

    if document_type not in recommendations:
        logger.warning(
            f"未知的文档类型: {document_type}，使用默认参数"
        )
        return recommendations["general"]

    chunk_size, overlap = recommendations[document_type]
    logger.info(
        f"📊 推荐的分块参数 ({document_type}): "
        f"chunk_size={chunk_size}, overlap={overlap}"
    )

    return chunk_size, overlap


def analyze_chunks(chunks: List[Document]) -> dict:
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