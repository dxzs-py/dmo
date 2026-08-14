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

    def build_multimodal_message_content(
        self,
        user_message: str,
        attachment_ids: list[int],
    ) -> list:
        """构造多模态消息内容（纯图片场景）。

        文本附件不再注入本消息——由 hint 引导 Agent 通过 attachment_rag_search /
        attachment_reader 工具按需检索，保持用户消息展示内容纯净。
        """
        from Django_xm.apps.tools.langchain.file_reader import read_attachment_as_base64

        content_parts = [{"type": "text", "text": user_message}]

        classified = self.classify_attachments(attachment_ids)
        image_ids = classified["image_ids"]

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
            "[Attachment] build_user_content: ids=%s, images=%s, texts=%s",
            attachment_ids,
            classified["image_ids"],
            classified["text_ids"],
        )

        if has_images and not has_text:
            return {
                "type": "multimodal",
                "content": self.build_multimodal_message_content(user_message, attachment_ids),
                "hint": None,
            }

        # 含文本附件：content 保持纯净（仅用户原始输入），文件内容通过 hint 引导工具检索
        names = self._get_attachment_names(attachment_ids)
        names_str = ", ".join(names) if names else f"{len(attachment_ids)}个文件"
        # 构造 "文件名 (id=N)" 标签，供 attachment_reader 工具获取真实附件 ID（LLM 无法从上下文自行推导）
        # names 与 attachment_ids 恒等长（_get_attachment_names 逐 id 生成），长度不一致即为 bug
        id_labels = ", ".join(f"{name} (id={att_id})" for name, att_id in zip(names, attachment_ids, strict=True))
        if self.should_use_rag(attachment_ids):
            hint = (
                f"用户上传了以下文件：{names_str}\n"
                f"优先使用 attachment_rag_search 工具检索文件内容（自动检索会话附件，无需指定ID）。"
                f"仅在检索无相关性时，才使用 attachment_reader 读取单个文件全文，"
                f"其 attachment_id 参数必须从以下对应关系取值：{id_labels}。"
            )
        else:
            hint = (
                f"用户上传了以下文件：{names_str}\n"
                f"请使用 attachment_reader 工具读取文件内容，"
                f"其 attachment_id 参数必须从以下对应关系取值：{id_labels}。"
            )

        if has_images and has_text:
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

        if has_text:
            return {"type": "text", "content": user_message, "hint": hint}

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
