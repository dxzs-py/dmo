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
    def process_tool_result(
        result_str: str,
        llm: BaseChatModel | None = None,
        max_length: int = 2000,
    ) -> str:
        if len(result_str) <= max_length:
            return result_str

        if RetrievalAugmenter.is_json(result_str):
            extracted = RetrievalAugmenter._extract_json_summary(result_str)
            if len(extracted) <= max_length:
                return extracted

        if llm is not None:
            try:
                summary = RetrievalAugmenter._summarize_with_llm(result_str, llm, max_length)
                if summary:
                    return summary
            except Exception:
                logger.exception("LLM 总结工具结果失败")

        return result_str[:max_length] + "\n[内容已截断]"

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
    def extract_json_keys(text: str) -> list[str]:
        try:
            data = json.loads(text.strip())
        except (json.JSONDecodeError, ValueError):
            return []

        if isinstance(data, dict):
            return list(data.keys())
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return list(data[0].keys())
        return []

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

    @staticmethod
    def _summarize_with_llm(text: str, llm: BaseChatModel, max_length: int) -> str | None:
        truncated_input = text[:8000] if len(text) > 8000 else text
        prompt = (
            f"请将以下工具返回结果总结为简洁的自然语言，保留关键信息和数据，不超过{max_length}字：\n\n{truncated_input}"
        )
        response = llm.invoke([{"role": "user", "content": prompt}])
        summary = getattr(response, "content", "")
        if summary and len(summary) > max_length:
            summary = summary[:max_length]
        return summary
