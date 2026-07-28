"""
Token 使用量追踪系统
支持分层 Token 追踪：LLM（输入/输出/推理/缓存）、工具调用、存储/Embedding
"""

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from Django_xm.apps.core.config import get_logger
from Django_xm.apps.ai_engine.config import settings as app_cfg

logger = get_logger(__name__)


@dataclass
class TokenRecord:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cached_input_tokens: int = 0
    cache_creation_tokens: int = 0
    category: str = "llm"
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "reasoningTokens": self.reasoning_tokens,
            "cachedInputTokens": self.cached_input_tokens,
            "cacheCreationTokens": self.cache_creation_tokens,
            "category": self.category,
            "timestamp": self.timestamp,
        }


@dataclass
class ToolUsage:
    name: str
    call_count: int = 1
    tokens: int = 0


@dataclass
class StorageUsage:
    embedding_tokens: int = 0
    retrieval_docs: int = 0


class TokenDetailTracker:
    """Token 追踪器 - 分层追踪 LLM / 工具 / 存储 Token 使用量"""

    def __init__(self, default_model: str | None = None):
        self.default_model = default_model or app_cfg.openai_model
        self.records: list[TokenRecord] = []
        self.tool_usages: dict[str, ToolUsage] = {}
        self.storage_usage = StorageUsage()
        self._current_record: TokenRecord | None = None
        self._lock = threading.Lock()

    def start_record(self, model: str | None = None, category: str = "llm") -> TokenRecord:
        model = model or self.default_model
        self._current_record = TokenRecord(model=model, category=category)
        return self._current_record

    def finish_record(self) -> TokenRecord | None:
        if self._current_record is None:
            return None
        record = self._current_record
        with self._lock:
            self.records.append(record)
        self._current_record = None
        return record

    @contextmanager
    def record(self, model_id: str, category: str = "llm", **kwargs):
        rec = self.start_record(model_id, category=category, **kwargs)
        try:
            yield rec
        finally:
            self.finish_record()

    def update_from_metadata(self, metadata: dict[str, Any], model: str | None = None):
        if not metadata:
            return

        if self._current_record is None:
            self.start_record(model)

        usage_meta = metadata.get("usage_metadata", {})

        if "input_tokens" in usage_meta:
            self._current_record.input_tokens += usage_meta["input_tokens"]
        if "output_tokens" in usage_meta:
            self._current_record.output_tokens += usage_meta["output_tokens"]
        if "reasoning_tokens" in usage_meta:
            self._current_record.reasoning_tokens += usage_meta["reasoning_tokens"]
        if "cached_tokens" in usage_meta:
            self._current_record.cached_input_tokens += usage_meta["cached_tokens"]
        if "cache_creation_input_tokens" in usage_meta:
            self._current_record.cache_creation_tokens += usage_meta["cache_creation_input_tokens"]

        if model and self._current_record.model != model:
            self._current_record.model = model

    def track_tool_usage(self, tool_name: str, tokens: int = 0):
        with self._lock:
            if tool_name in self.tool_usages:
                self.tool_usages[tool_name].call_count += 1
                self.tool_usages[tool_name].tokens += tokens
            else:
                self.tool_usages[tool_name] = ToolUsage(name=tool_name, tokens=tokens)

    def track_embedding(self, tokens: int, docs: int = 0):
        with self._lock:
            self.storage_usage.embedding_tokens += tokens
            self.storage_usage.retrieval_docs += docs

    def get_total_tokens(self) -> dict[str, int]:
        totals = {
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "cached_input_tokens": 0,
            "cache_creation_tokens": 0,
        }
        for record in self.records:
            totals["input_tokens"] += record.input_tokens
            totals["output_tokens"] += record.output_tokens
            totals["reasoning_tokens"] += record.reasoning_tokens
            totals["cached_input_tokens"] += record.cached_input_tokens
            totals["cache_creation_tokens"] += record.cache_creation_tokens
        return totals

    def get_summary(self) -> dict[str, Any]:
        totals = self.get_total_tokens()
        return {
            "tokens": {
                "input": totals["input_tokens"],
                "output": totals["output_tokens"],
                "reasoning": totals["reasoning_tokens"],
                "cachedInput": totals["cached_input_tokens"],
                "cacheCreation": totals["cache_creation_tokens"],
                "total": (
                    totals["input_tokens"]
                    + totals["output_tokens"]
                    + totals["reasoning_tokens"]
                ),
            },
            "recordCount": len(self.records),
            "models": list(set(r.model for r in self.records)),
            "records": [r.to_dict() for r in self.records[-10:]],
        }

    def get_token_detail(self) -> dict[str, Any]:
        llm_totals = {"input": 0, "output": 0, "reasoning": 0, "cachedInput": 0, "cacheCreation": 0}
        tool_llm_totals = {"input": 0, "output": 0}

        for record in self.records:
            if record.category == "tool":
                tool_llm_totals["input"] += record.input_tokens
                tool_llm_totals["output"] += record.output_tokens
            else:
                llm_totals["input"] += record.input_tokens
                llm_totals["output"] += record.output_tokens
                llm_totals["reasoning"] += record.reasoning_tokens
                llm_totals["cachedInput"] += record.cached_input_tokens
                llm_totals["cacheCreation"] += record.cache_creation_tokens

        llm_totals["total"] = llm_totals["input"] + llm_totals["output"] + llm_totals["reasoning"]

        tool_info = {
            "count": sum(t.call_count for t in self.tool_usages.values()),
            "names": list(self.tool_usages.keys()),
            "tokens": sum(t.tokens for t in self.tool_usages.values()),
            "llmTokens": {
                "input": tool_llm_totals["input"],
                "output": tool_llm_totals["output"],
            },
        }

        storage_info = {
            "embeddingTokens": self.storage_usage.embedding_tokens,
            "retrievalDocs": self.storage_usage.retrieval_docs,
        }

        return {
            "llm": llm_totals,
            "tools": tool_info,
            "storage": storage_info,
        }

    def log_summary(self):
        summary = self.get_summary()
        detail = self.get_token_detail()
        parts = [
            "📊 Token统计:",
            f"LLM 输入={detail['llm']['input']}, 输出={detail['llm']['output']}, 推理={detail['llm']['reasoning']}",
        ]
        if detail["tools"]["count"] > 0:
            parts.append(f"工具 调用={detail['tools']['count']}次, Token={detail['tools']['tokens']}")
        if detail["storage"]["embeddingTokens"] > 0:
            parts.append(f"存储 Embedding={detail['storage']['embeddingTokens']}, 检索文档={detail['storage']['retrievalDocs']}")
        parts.append(f"总调用次数: {summary['recordCount']}")
        logger.info(" | ".join(parts))


def create_token_detail_tracker(model: str | None = None) -> TokenDetailTracker:
    model = model or app_cfg.openai_model
    return TokenDetailTracker(default_model=model)
