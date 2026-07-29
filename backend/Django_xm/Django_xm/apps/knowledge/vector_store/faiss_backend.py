"""FAISS 向量存储后端实现

基于文件系统的 FAISS 向量存储，支持完整性校验。
"""

import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from .base import VectorStoreBackend

logger = logging.getLogger(__name__)


def _validate_faiss_index_integrity(index_path: Path) -> bool:
    """验证 FAISS 索引文件的完整性，防止篡改"""
    integrity_file = index_path / ".integrity"
    if not integrity_file.exists():
        logger.warning(f"FAISS 索引缺少完整性校验文件: {index_path}")
        return False

    try:
        with open(integrity_file, encoding="utf-8") as f:
            stored_hashes = json.load(f)

        for filename, expected_hash in stored_hashes.items():
            file_path = index_path / filename
            if not file_path.exists():
                logger.error(f"FAISS 索引文件缺失: {filename}")
                return False
            actual_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
            if actual_hash != expected_hash:
                logger.error(f"FAISS 索引文件被篡改: {filename}")
                return False

        return True
    except Exception:
        logger.exception("完整性校验失败")
        return False


def _save_faiss_integrity(index_path: Path) -> None:
    """保存 FAISS 索引文件的完整性校验"""
    integrity_file = index_path / ".integrity"
    hashes: dict[str, str] = {}

    for file_path in index_path.iterdir():
        if file_path.name in {".integrity", "metadata.json"}:
            continue
        if file_path.is_file():
            hashes[file_path.name] = hashlib.sha256(file_path.read_bytes()).hexdigest()

    with open(integrity_file, "w", encoding="utf-8") as f:
        json.dump(hashes, f, indent=2)


class FAISSBackend(VectorStoreBackend):
    """FAISS 向量存储后端"""

    def __init__(self, base_path: str):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_collection_path(self, collection_name: str) -> Path:
        return self.base_path / collection_name

    @property
    def store_type(self) -> str:
        return "faiss"

    def create(
        self,
        documents: list[Document],
        embeddings: Embeddings,
        collection_name: str,
        **kwargs: Any,
    ) -> VectorStore:
        from langchain_community.vectorstores import FAISS

        vector_store = FAISS.from_documents(
            documents=documents,
            embedding=embeddings,
            **kwargs,
        )

        # 保存到磁盘
        collection_path = self._get_collection_path(collection_name)
        collection_path.mkdir(parents=True, exist_ok=True)
        vector_store.save_local(str(collection_path))
        _save_faiss_integrity(collection_path)

        logger.info(f"FAISS 向量库创建成功 (collection={collection_name})")
        return vector_store

    def load(
        self,
        embeddings: Embeddings,
        collection_name: str,
        **kwargs: Any,
    ) -> VectorStore:
        from langchain_community.vectorstores import FAISS

        collection_path = self._get_collection_path(collection_name)
        if not collection_path.exists():
            raise FileNotFoundError(f"FAISS 向量库路径不存在: {collection_path}")

        if _validate_faiss_index_integrity(collection_path):
            logger.info("FAISS 索引完整性校验通过，安全加载")
            vector_store = FAISS.load_local(
                folder_path=str(collection_path),
                embeddings=embeddings,
                allow_dangerous_deserialization=True,
                **kwargs,
            )
        else:
            raise ValueError(
                "FAISS 索引完整性校验失败。"
                "索引可能被篡改或缺少校验文件(.integrity)。"
                "请重新构建索引，或迁移到 Chroma 以避免此安全风险。"
            )

        logger.info(f"FAISS 向量库加载成功 (collection={collection_name})")
        return vector_store

    def save(
        self,
        vector_store: VectorStore,
        path: str,
        **kwargs: Any,
    ) -> None:
        save_path = Path(path)
        save_path.parent.mkdir(parents=True, exist_ok=True)

        if hasattr(vector_store, "save_local"):
            vector_store.save_local(str(save_path))
            _save_faiss_integrity(save_path)
            logger.info("FAISS 向量库保存成功（含完整性校验）")
        else:
            logger.warning("向量库不支持 save_local")

    def delete(self, collection_name: str) -> bool:
        collection_path = self._get_collection_path(collection_name)
        if not collection_path.exists():
            logger.warning(f"FAISS 向量库不存在: {collection_name}")
            return False

        try:
            shutil.rmtree(collection_path)
            logger.info(f"FAISS 向量库删除成功: {collection_name}")
            return True
        except Exception:
            logger.exception("FAISS 向量库删除失败")
            return False

    def list_collections(self, prefix: str = "") -> list[str]:
        if not self.base_path.exists():
            return []

        collections: list[str] = []
        for item in self.base_path.iterdir():
            if item.is_dir() and (item / ".integrity").exists():
                name = item.name
                if not prefix or name.startswith(prefix):
                    collections.append(name)
        return sorted(collections)

    def exists(self, collection_name: str) -> bool:
        collection_path = self._get_collection_path(collection_name)
        return collection_path.exists() and (collection_path / ".integrity").exists()

    def add_documents(
        self,
        vector_store: VectorStore,
        documents: list[Document],
    ) -> list[str]:
        if hasattr(vector_store, "add_documents"):
            ids = vector_store.add_documents(documents)
        elif hasattr(vector_store, "add_texts"):
            texts = [doc.page_content for doc in documents]
            metadatas = [doc.metadata for doc in documents]
            ids = vector_store.add_texts(texts, metadatas)
        else:
            raise ValueError("FAISS 向量库不支持添加文档")
        logger.info(f"FAISS 添加 {len(ids)} 个文档")
        return ids

    def remove_documents(
        self,
        collection_name: str,
        document_ids: list[str],
    ) -> bool:
        """FAISS 不支持按 ID 直接删除，需要重建索引"""
        logger.warning("FAISS 不支持按 ID 直接删除文档，请使用 remove_documents_by_metadata")
        return False

    def remove_documents_by_metadata(
        self,
        collection_name: str,
        key: str,
        value: str,
    ) -> int:
        """按元数据删除文档：遍历 docstore 匹配 metadata 后删除"""
        collection_path = self._get_collection_path(collection_name)
        if not collection_path.exists():
            logger.warning(f"FAISS 向量库不存在: {collection_name}")
            return 0

        # FAISS 的删除需要加载后操作，此处返回 0 提示调用方使用 load + delete 流程
        logger.warning("FAISS 按元数据删除需要加载向量库后操作，建议通过 load -> 遍历 docstore -> delete 流程处理")
        return 0

    def search(
        self,
        vector_store: VectorStore,
        query: str,
        k: int = 4,
        filter: dict | None = None,
    ) -> list[tuple[Document, float]]:
        kwargs: dict[str, Any] = {"k": k}
        if filter:
            kwargs["filter"] = filter
        return vector_store.similarity_search_with_score(query=query, **kwargs)

    def get_stats(self, collection_name: str) -> dict[str, Any]:
        collection_path = self._get_collection_path(collection_name)
        if not collection_path.exists():
            return {
                "name": collection_name,
                "exists": False,
                "store_type": "faiss",
            }

        # 尝试读取元数据文件
        metadata_path = collection_path / "metadata.json"
        metadata: dict[str, Any] = {}
        if metadata_path.exists():
            try:
                with open(metadata_path, encoding="utf-8") as f:
                    metadata = json.load(f)
            except Exception:
                # metadata.json 解析失败时回退到空字典，使用默认值
                logger.debug("metadata.json 解析失败，回退到空字典")

        return {
            "name": collection_name,
            "exists": True,
            "store_type": "faiss",
            "num_documents": metadata.get("num_documents", 0),
            "path": str(collection_path),
        }
