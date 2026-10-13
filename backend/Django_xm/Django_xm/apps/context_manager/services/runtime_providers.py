"""
context_manager 跨层依赖的运行期注入点（环 1 依赖倒置）

背景（spec eliminate-priority-debts 任务 4.1 / agent_audit_arch §4.2）：本 app 禁止
import ai_engine（.importlinter 契约 context-manager-not-dependent-on-ai-engine）、
并不得经 knowledge/attachments 等模块传递触达 ai_engine。模型解析链、Store 工厂、
ToolUsageGuard 单例、Embedding 服务、文档记忆上下文等外部能力一律改为**运行期
provider 注入**：

- ai_engine AppConfig.ready() 注册：compression/evaluation 模型解析链（bridge）、
  Store 工厂、ToolUsageGuard 单例；
- context_manager AppConfig.ready() 注册：Embedding 服务（knowledge 侧，合法下行）；
- attachments AppConfig.ready() 注册：文档记忆上下文构建（attachments 自家服务）。

未注册时的语义：各消费点按既有降级路径处理（评估默认评分 / Store 不可用返回
None / guard 交接跳过 / Embedding 相似度回退词重叠 / 文档上下文跳过注入）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# 压缩模型解析器：入参为 context_settings.compression_lightweight_model 配置值
# （可空），返回解析后的压缩/摘要模型实例（解析链在 ai_engine 侧 bridge 承载）。
CompressionModelProvider = Callable[[str | None], Any]
# 摘要质量评估模型解析器：无入参，返回 helper 或默认模型实例。
EvaluationModelProvider = Callable[[], Any]
# LangGraph Store 工厂：等价 ai_engine checkpointer_factory.get_store。
StoreProvider = Callable[[], Any]
# ToolUsageGuard 单例获取器。
ToolUsageGuardProvider = Callable[[], Any]
# Embedding 服务获取器：返回具备 embed_query 的 embeddings 实例。
EmbeddingProvider = Callable[[], Any]
# 文档记忆上下文构建器：入参 (user_id, store)，返回上下文文本（可空）。
DocumentContextProvider = Callable[[Any, Any], str | None]

_compression_model_provider: CompressionModelProvider | None = None
_evaluation_model_provider: EvaluationModelProvider | None = None
_store_provider: StoreProvider | None = None
_tool_usage_guard_provider: ToolUsageGuardProvider | None = None
_embedding_provider: EmbeddingProvider | None = None
_document_context_provider: DocumentContextProvider | None = None


def set_compression_model_provider(provider: CompressionModelProvider) -> None:
    global _compression_model_provider  # noqa: PLW0603 - 运行期 provider 注册点（先例 tool_usage_guard._guard_instance）
    _compression_model_provider = provider


def get_compression_model_provider() -> CompressionModelProvider | None:
    return _compression_model_provider


def set_evaluation_model_provider(provider: EvaluationModelProvider) -> None:
    global _evaluation_model_provider  # noqa: PLW0603 - 运行期 provider 注册点（先例 tool_usage_guard._guard_instance）
    _evaluation_model_provider = provider


def get_evaluation_model_provider() -> EvaluationModelProvider | None:
    return _evaluation_model_provider


def set_store_provider(provider: StoreProvider) -> None:
    global _store_provider  # noqa: PLW0603 - 运行期 provider 注册点（先例 tool_usage_guard._guard_instance）
    _store_provider = provider


def get_store_provider() -> StoreProvider | None:
    return _store_provider


def set_tool_usage_guard_provider(provider: ToolUsageGuardProvider) -> None:
    global _tool_usage_guard_provider  # noqa: PLW0603 - 运行期 provider 注册点（先例 tool_usage_guard._guard_instance）
    _tool_usage_guard_provider = provider


def get_tool_usage_guard_provider() -> ToolUsageGuardProvider | None:
    return _tool_usage_guard_provider


def set_embedding_provider(provider: EmbeddingProvider) -> None:
    global _embedding_provider  # noqa: PLW0603 - 运行期 provider 注册点（先例 tool_usage_guard._guard_instance）
    _embedding_provider = provider


def get_embedding_provider() -> EmbeddingProvider | None:
    return _embedding_provider


def set_document_context_provider(provider: DocumentContextProvider) -> None:
    global _document_context_provider  # noqa: PLW0603 - 运行期 provider 注册点（先例 tool_usage_guard._guard_instance）
    _document_context_provider = provider


def get_document_context_provider() -> DocumentContextProvider | None:
    return _document_context_provider
