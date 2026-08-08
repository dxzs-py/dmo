"""
附件内容加载服务
支持多模态消息和 RAG 检索增强
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class AttachmentService:
    RAG_CONTENT_THRESHOLD = 30000

    def classify_attachments(self, attachment_ids: list[int]) -> dict[str, list[int]]:
        image_ids = []
        text_ids = []

        for att_id in attachment_ids:
            try:
                from Django_xm.apps.tools.langchain.file_reader import get_attachment_info

                info = get_attachment_info(att_id)
                if info.get("is_image"):
                    image_ids.append(att_id)
                else:
                    text_ids.append(att_id)
            except Exception as e:
                logger.warning(f"获取附件信息失败 (id={att_id}): {e}")
                text_ids.append(att_id)

        return {"image_ids": image_ids, "text_ids": text_ids}

    def load_text_attachment_contents(self, attachment_ids: list[int]) -> str | None:
        if not attachment_ids:
            return None

        try:
            from Django_xm.apps.tools.langchain.file_reader import read_multiple_attachments

            content = read_multiple_attachments(attachment_ids)
            if content:
                logger.info(f"成功加载 {len(attachment_ids)} 个文本附件内容，共 {len(content)} 字符")
            return content if content.strip() else None
        except Exception:
            logger.exception("加载文本附件内容失败")
            return None

    def load_attachment_contents(self, attachment_ids: list[int]) -> str | None:
        return self.load_text_attachment_contents(attachment_ids)

    def build_message_with_attachments(self, user_message: str, attachment_content: str | None) -> str:
        if not attachment_content:
            return user_message

        return (
            f"{user_message}\n\n"
            f"---\n"
            f"以下是用户上传的文件内容（已直接包含在本消息中，无需使用工具读取，请直接基于以下内容分析和回答）：\n\n"
            f"{attachment_content}\n"
            f"---\n"
        )

    def build_multimodal_message_content(
        self,
        user_message: str,
        attachment_ids: list[int],
    ) -> list:
        from Django_xm.apps.tools.langchain.file_reader import read_attachment_as_base64

        content_parts = [{"type": "text", "text": user_message}]

        classified = self.classify_attachments(attachment_ids)
        image_ids = classified["image_ids"]
        text_ids = classified["text_ids"]

        if text_ids:
            text_content = self.load_text_attachment_contents(text_ids)
            if text_content:
                content_parts.append(
                    {
                        "type": "text",
                        "text": (
                            f"\n\n---\n以下是用户上传的文件内容（已直接包含在本消息中，无需使用工具读取，请直接基于以下内容分析和回答）：\n\n"
                            f"{text_content}\n---\n"
                        ),
                    }
                )

        for img_id in image_ids:
            try:
                image_data, mime_type = read_attachment_as_base64(img_id)
                content_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{image_data}"},
                    }
                )
                logger.info(f"图片附件 {img_id} 已构造为多模态消息")
            except Exception as e:
                logger.exception(f"构造图片多模态消息失败 (id={img_id})")
                content_parts.append(
                    {
                        "type": "text",
                        "text": f"\n[图片附件 (id={img_id}) 加载失败: {e!s}]",
                    }
                )

        return content_parts

    def build_rag_enhanced_message(
        self,
        user_message: str,
        attachment_ids: list[int],
        progress_callback=None,
    ) -> str:
        from Django_xm.apps.knowledge.services.embedding_service import get_embeddings
        from Django_xm.apps.tools.langchain.file_reader import read_attachment_as_documents

        all_docs = []
        classified = self.classify_attachments(attachment_ids)
        image_ids = classified["image_ids"]
        text_ids = classified["text_ids"]

        if progress_callback:
            progress_callback("reading", "正在读取文档内容...")

        for att_id in text_ids:
            try:
                docs = read_attachment_as_documents(att_id)
                all_docs.extend(docs)
                logger.info(f"附件 {att_id} 生成 {len(docs)} 个文档片段")
            except Exception:
                logger.exception(f"读取附件文档失败 (id={att_id})")

        if not all_docs:
            return self.build_message_with_attachments(user_message, self.load_text_attachment_contents(text_ids))

        try:
            if progress_callback:
                progress_callback("indexing", "正在建立向量索引...")
            from langchain_core.vectorstores import InMemoryVectorStore

            embeddings = get_embeddings()
            vector_store = InMemoryVectorStore.from_documents(all_docs, embeddings)

            if progress_callback:
                progress_callback("searching", "正在进行相关信息检索...")
            retriever = vector_store.as_retriever(search_type="similarity", search_kwargs={"k": 6})
            relevant_docs = retriever.invoke(user_message)

            rag_content = "\n\n".join(
                f"[来源: {doc.metadata.get('original_name', doc.metadata.get('source', '未知'))}]\n{doc.page_content}"
                for doc in relevant_docs
            )

            logger.info(f"RAG 检索到 {len(relevant_docs)} 个相关片段（共 {len(all_docs)} 个片段）")

            image_note = ""
            if image_ids:
                image_note = f"\n\n注意：用户还上传了 {len(image_ids)} 张图片，图片内容已作为多模态消息直接传递给模型。"

            if progress_callback:
                progress_callback("complete", "文档处理完成")

            return (
                f"{user_message}\n\n"
                f"---\n"
                f"以下是用户上传文件中与问题最相关的内容（RAG 检索结果）：\n\n"
                f"{rag_content}\n"
                f"---\n"
                f"[原始文件共 {len(all_docs)} 个片段，已检索最相关的 {len(relevant_docs)} 个]{image_note}"
            )
        except Exception:
            logger.exception("RAG 检索失败，回退到直接注入")
            return self.build_message_with_attachments(user_message, self.load_text_attachment_contents(text_ids))

    def should_use_rag(self, attachment_ids: list[int]) -> bool:
        from Django_xm.apps.tools.langchain.file_reader import get_attachment_info

        total_size = 0
        text_count = 0

        for att_id in attachment_ids:
            try:
                info = get_attachment_info(att_id)
                if not info.get("is_image"):
                    total_size += info.get("file_size", 0)
                    text_count += 1
            except Exception:
                text_count += 1

        if text_count > 3:
            return True

        return total_size > self.RAG_CONTENT_THRESHOLD

    def has_images(self, attachment_ids: list[int]) -> bool:
        classified = self.classify_attachments(attachment_ids)
        return len(classified["image_ids"]) > 0

    def _get_attachment_names(self, attachment_ids: list[int]) -> list[str]:
        """获取附件文件名列表，用于元信息提示。"""
        names = []
        for att_id in attachment_ids:
            try:
                from Django_xm.apps.tools.langchain.file_reader import get_attachment_info

                info = get_attachment_info(att_id)
                names.append(info.get("original_name", f"附件{att_id}"))
            except Exception:
                names.append(f"附件{att_id}")
        return names

    def build_user_content(
        self,
        user_message: str,
        attachment_ids: list[int],
        progress_callback=None,
    ) -> dict[str, Any]:
        """构建用户消息内容。

        返回格式: {"type": "text"|"multimodal", "content": ..., "hint": str|None}
        - content: 用户原始输入（纯文本/多模态），用于展示与持久化
        - hint: LLM 引导文本（附件检索工具说明），不展示给用户，仅注入 LLM 上下文
        """
        if not attachment_ids:
            logger.info("[Attachment] build_user_content: 无附件ID")
            return {"type": "text", "content": user_message, "hint": None}

        classified = self.classify_attachments(attachment_ids)
        has_images = len(classified["image_ids"]) > 0
        has_text = len(classified["text_ids"]) > 0
        logger.info(
            f"[Attachment] build_user_content: ids={attachment_ids}, images={classified['image_ids']}, texts={classified['text_ids']}"
        )

        if has_images and not has_text:
            return {
                "type": "multimodal",
                "content": self.build_multimodal_message_content(user_message, attachment_ids),
                "hint": None,
            }

        if has_images and has_text:
            use_rag = self.should_use_rag(attachment_ids)
            if use_rag:
                hints = self._get_attachment_names(attachment_ids)
                names_str = ", ".join(hints) if hints else f"{len(attachment_ids)}个文件"
                hint = (
                    f"用户上传了以下文件：{names_str}\n"
                    f"优先使用 attachment_rag_search 工具检索文件内容（支持向量相似度搜索，适合大文件）。"
                    f"仅在检索无相关性时，才使用 attachment_reader 读取单个文件全文。"
                )
                image_ids = classified["image_ids"]
                from Django_xm.apps.tools.langchain.file_reader import read_attachment_as_base64

                content_parts = [{"type": "text", "text": user_message}]
                for img_id in image_ids:
                    try:
                        image_data, mime_type = read_attachment_as_base64(img_id)
                        content_parts.append(
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{mime_type};base64,{image_data}"},
                            }
                        )
                    except Exception:
                        logger.exception(f"构造图片多模态消息失败 (id={img_id})")

                return {"type": "multimodal", "content": content_parts, "hint": hint}
            else:
                return {
                    "type": "multimodal",
                    "content": self.build_multimodal_message_content(user_message, attachment_ids),
                    "hint": None,
                }

        if has_text:
            if self.should_use_rag(attachment_ids):
                names = self._get_attachment_names(attachment_ids)
                names_str = ", ".join(names) if names else f"{len(attachment_ids)}个文件"
                hint = (
                    f"用户上传了以下文件：{names_str}\n"
                    f"优先使用 attachment_rag_search 工具检索文件内容（支持向量相似度搜索，适合大文件）。"
                    f"仅在检索无相关性时，才使用 attachment_reader 读取单个文件全文。"
                )
                return {"type": "text", "content": user_message, "hint": hint}
            else:
                text_content = self.load_text_attachment_contents(attachment_ids)
                return {
                    "type": "text",
                    "content": self.build_message_with_attachments(user_message, text_content),
                    "hint": None,
                }

        return {"type": "text", "content": user_message, "hint": None}

    def link_attachments_to_message(self, message, attachment_ids: list[int] | None):
        if not attachment_ids:
            return

        from Django_xm.apps.attachments.models import ChatAttachment

        ChatAttachment.objects.filter(id__in=attachment_ids, session=message.session, message__isnull=True).update(
            message=message
        )

    def persist_attachments_to_store(
        self,
        user_id: int,
        attachment_ids: list[int],
        store=None,
    ) -> int:
        if not attachment_ids or not user_id:
            return 0

        from Django_xm.apps.attachments.services.document_memory_service import DocumentMemoryService

        service = DocumentMemoryService(store=store)
        saved_count = 0

        for att_id in attachment_ids:
            try:
                from Django_xm.apps.tools.langchain.file_reader import get_attachment_info

                info = get_attachment_info(att_id)
                if info.get("is_image"):
                    continue

                doc_name = info.get("original_name", f"attachment_{att_id}")
                file_size = info.get("file_size", 0)
                file_type = info.get("content_type", "")

                content = self.load_text_attachment_contents([att_id])
                if not content or not content.strip():
                    continue

                saved = service.save_document(
                    user_id=user_id,
                    attachment_id=att_id,
                    doc_name=doc_name,
                    content=content,
                    metadata={"file_size": file_size, "file_type": file_type},
                )
                if saved:
                    saved_count += 1
            except Exception:
                logger.exception(f"附件 {att_id} 持久化到 Store 失败")

        if saved_count > 0:
            logger.info(f"已将 {saved_count}/{len(attachment_ids)} 个附件持久化到 Store (user={user_id})")
        return saved_count
