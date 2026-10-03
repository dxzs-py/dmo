"""
RAG 检索增强与工具结果后处理

1. HyDE 查询改写 - 使用 LLM 生成假设性文档，用假设文档的 Embedding 进行检索
2. 工具结果后处理 - 对大型 JSON 或原始页面进行关键信息提取和总结

参考：
- https://python.langchain.com/docs/concepts/retrieval/
- https://arxiv.org/abs/2212.10496 (HyDE)
"""

import json

from langchain_core.language_models import BaseChatModel

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)


class RetrievalAugmenter:
    @staticmethod
    async def hyde_rewrite(query: str, llm: BaseChatModel) -> str:
        prompt = f"请写一段详细的回答来回答以下问题：{query}"
        try:
            response = await llm.ainvoke([{"role": "user", "content": prompt}])
            hypothetical_doc = getattr(response, "content", "")
            if not hypothetical_doc:
                logger.warning("HyDE 改写返回空内容，回退到原始查询")
                return query
            logger.info(f"HyDE 改写完成: query='{query[:50]}...' -> doc_len={len(hypothetical_doc)}")
            return hypothetical_doc
        except Exception:
            logger.exception("HyDE 改写失败")
            return query

    @staticmethod
    def hyde_rewrite_sync(query: str, llm: BaseChatModel) -> str:
        prompt = f"请写一段详细的回答来回答以下问题：{query}"
        try:
            response = llm.invoke([{"role": "user", "content": prompt}])
            hypothetical_doc = getattr(response, "content", "")
            if not hypothetical_doc:
                logger.warning("HyDE 改写返回空内容，回退到原始查询")
                return query
            logger.info(f"HyDE 改写完成: query='{query[:50]}...' -> doc_len={len(hypothetical_doc)}")
            return hypothetical_doc
        except Exception:
            logger.exception("HyDE 改写失败")
            return query

    @staticmethod
    def is_json(text: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return False
        if stripped[0] not in ("{", "["):
            return False
        try:
            json.loads(stripped)
            return True
        except (json.JSONDecodeError, ValueError):
            return False

    @staticmethod
    def _extract_json_summary(text: str) -> str:
        try:
            data = json.loads(text.strip())
        except (json.JSONDecodeError, ValueError):
            return text

        if isinstance(data, dict):
            lines = []
            for key, value in data.items():
                value_str = str(value)
                if len(value_str) > 300:
                    value_str = value_str[:300] + "..."
                lines.append(f"{key}: {value_str}")
            return "\n".join(lines)

        if isinstance(data, list):
            if not data:
                return "[]"
            if isinstance(data[0], dict):
                keys = list(data[0].keys())
                lines = [f"共 {len(data)} 条记录，字段: {', '.join(keys)}"]
                for i, item in enumerate(data[:5]):
                    item_parts = []
                    for k in keys[:6]:
                        v = str(item.get(k, ""))
                        if len(v) > 150:
                            v = v[:150] + "..."
                        item_parts.append(f"{k}={v}")
                    lines.append(f"  [{i}] {', '.join(item_parts)}")
                if len(data) > 5:
                    lines.append(f"  ... 还有 {len(data) - 5} 条记录")
                return "\n".join(lines)
            items = [str(item)[:200] for item in data[:10]]
            result = f"共 {len(data)} 项:\n" + "\n".join(items)
            if len(data) > 10:
                result += f"\n... 还有 {len(data) - 10} 项"
            return result

        return text
