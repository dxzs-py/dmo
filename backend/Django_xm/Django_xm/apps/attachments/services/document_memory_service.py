"""
文档记忆服务

将用户上传的文档内容持久化到 LangGraph Store，
实现跨会话的文档记忆和自动摘要生成。

核心能力：
1. 文档上传后自动存储到 Store（文档名称、内容摘要、元数据）
2. 跨会话检索用户已上传的文档信息
3. 自动调用 LLM 生成文档摘要
4. 在 Agent 对话中动态注入文档上下文

参考：
- https://docs.langchain.com/oss/python/langchain/long-term-memory
- https://docs.langchain.com/oss/python/langgraph/persistence#memory-store
"""

import logging
from typing import Optional, Dict, Any, List

from Django_xm.apps.config_center.config import get_logger

logger = get_logger(__name__)


class DocumentMemoryService:
    """文档记忆服务 - 基于 LangGraph Store 实现跨会话文档记忆"""

    def __init__(self, store=None):
        self._store = store

    def _ensure_store(self):
        from Django_xm.apps.ai_engine.services.checkpointer_factory import ensure_store
        return ensure_store(self)

    @staticmethod
    def _build_namespace(user_id: int) -> tuple:
        return (str(user_id), "documents")

    def save_document(
        self,
        user_id: int,
        attachment_id: int,
        doc_name: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        summary: Optional[str] = None,
    ) -> bool:
        store = self._ensure_store()
        if store is None:
            logger.warning("Store 不可用，跳过文档记忆存储")
            return False

        namespace = self._build_namespace(user_id)
        doc_data = {
            "name": doc_name,
            "content_length": len(content) if content else 0,
            "summary": summary or "",
            "metadata": metadata or {},
        }

        if not doc_data["summary"] and content:
            doc_data["summary"] = self._generate_summary(content, doc_name)

        try:
            store.put(namespace, str(attachment_id), doc_data)
            logger.info(
                f"文档已存储到 Store: user={user_id}, attachment={attachment_id}, "
                f"name={doc_name}, summary_len={len(doc_data['summary'])}"
            )
            return True
        except Exception as e:
            logger.error(f"文档存储到 Store 失败: {e}")
            return False

    def get_document(self, user_id: int, attachment_id: int) -> Optional[Dict[str, Any]]:
        store = self._ensure_store()
        if store is None:
            return None

        namespace = self._build_namespace(user_id)
        try:
            item = store.get(namespace, str(attachment_id))
            return item.value if item else None
        except Exception as e:
            logger.error(f"从 Store 获取文档失败: {e}")
            return None

    def list_documents(self, user_id: int) -> List[Dict[str, Any]]:
        store = self._ensure_store()
        if store is None:
            return []

        namespace = self._build_namespace(user_id)
        try:
            items = store.search(namespace)
            docs = []
            for item in items:
                doc = item.value if hasattr(item, 'value') else item
                doc["store_key"] = item.key if hasattr(item, 'key') else None
                docs.append(doc)
            return docs
        except Exception as e:
            logger.error(f"从 Store 搜索文档失败: {e}")
            return []

    def delete_document(self, user_id: int, attachment_id: int) -> bool:
        store = self._ensure_store()
        if store is None:
            return False

        namespace = self._build_namespace(user_id)
        try:
            store.delete(namespace, str(attachment_id))
            logger.info(f"文档已从 Store 删除: user={user_id}, attachment={attachment_id}")
            return True
        except Exception as e:
            logger.error(f"从 Store 删除文档失败: {e}")
            return False

    def build_document_context(self, user_id: int) -> str:
        docs = self.list_documents(user_id)
        if not docs:
            return ""

        lines = ["用户已上传的文档："]
        for i, doc in enumerate(docs, 1):
            name = doc.get("name", "未知文档")
            summary = doc.get("summary", "无摘要")
            content_length = doc.get("content_length", 0)
            lines.append(f"{i}. {name}（{content_length} 字符）：{summary}")

        return "\n".join(lines)

    @staticmethod
    def _generate_summary(content: str, doc_name: str = "") -> str:
        if not content or not content.strip():
            return ""

        truncated = content[:8000] if len(content) > 8000 else content

        try:
            from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model
            model = get_chat_model()
            prompt = (
                f"请为以下文档内容生成一段简洁的摘要（不超过200字），"
                f"包含文档的核心主题、关键信息和主要结论。\n\n"
                f"文档名称：{doc_name}\n\n"
                f"文档内容：\n{truncated}\n\n摘要："
            )
            response = model.invoke([{"role": "user", "content": prompt}])
            summary = getattr(response, "content", "")
            if summary and len(summary) > 500:
                summary = summary[:500]
            return summary or ""
        except Exception as e:
            logger.error(f"生成文档摘要失败: {e}")
            fallback = truncated[:200].replace("\n", " ").strip()
            return f"[自动截取] {fallback}..."


def on_attachment_uploaded(
    user_id: int,
    attachment_id: int,
    doc_name: str,
    content: str,
    file_size: int = 0,
    file_type: str = "",
    store=None,
) -> bool:
    service = DocumentMemoryService(store=store)
    metadata = {
        "file_size": file_size,
        "file_type": file_type,
    }
    return service.save_document(
        user_id=user_id,
        attachment_id=attachment_id,
        doc_name=doc_name,
        content=content,
        metadata=metadata,
    )


def get_user_document_context(user_id: int, store=None) -> str:
    service = DocumentMemoryService(store=store)
    return service.build_document_context(user_id)
