"""
AI 引擎配置模块

管理所有 AI 相关配置项，包括：
- LLM 提供商（OpenAI / Anthropic / DeepSeek / Groq / 百度千帆）
- Agent / RAG / Embedding / Vector Store
- Guardrails / Checkpointer / Store / Summarization
- Agent Cache / LangSmith
- Tavily / 高德地图

项目级配置（DB/Redis/Celery/安全/日志/服务器等）
在 Django_xm.apps.core.config 中管理。

使用方式:
    from Django_xm.apps.ai_engine.config import settings
    api_key = settings.openai_api_key
"""

from __future__ import annotations

import threading
from typing import Any

from pydantic import Field

from Django_xm.apps.core.config import (
    ProjectSettings,
)

# ==================== Agent 递归深度常量（3A-P2-1）====================
# LangGraph graph invoke/astream 的 recursion_limit 三档取值（代码级常量，
# 不进 settings：取值为执行拓扑的工程判断而非运维可调项，避免配置面扩大）。
# 置于 ai_engine 配置模块（底层引擎，chat/research/agent_hub 均可合法依赖）。
# 取值依据：
# - CHAT=500：单轮对话 agent，工具循环步数适中；
# - RESEARCH=1000：深度研究多轮规划 + 子代理编排，链路更深；
# - SUBAGENT=200：子代理任务面窄而浅，超限由 GraphRecursionError 兜底。
AGENT_RECURSION_LIMIT_CHAT = 500
AGENT_RECURSION_LIMIT_RESEARCH = 1000
AGENT_RECURSION_LIMIT_SUBAGENT = 200


class Settings(ProjectSettings):
    """
    AI 引擎配置类

    继承 ProjectSettings，在项目级配置基础上添加 AI 专属配置项。
    """

    # ==================== OpenAI 配置 ====================
    openai_api_key: str = Field(default="", description="OpenAI API 密钥")

    openai_api_base: str = Field(default="https://api.openai.com/v1", description="OpenAI API 基础 URL")

    openai_model: str = Field(default="gpt-4o-mini", description="默认 OpenAI 模型")

    openai_temperature: float = Field(default=0.7, ge=0.0, le=1.0, description="模型温度参数")

    openai_max_tokens: int | None = Field(default=None, description="最大生成 token 数")

    openai_streaming: bool = Field(default=True, description="是否默认启用流式输出")

    # ==================== Anthropic 配置 ====================
    anthropic_api_key: str = Field(default="", description="Anthropic API 密钥")

    # ==================== DeepSeek 配置 ====================
    deepseek_api_key: str = Field(default="", description="DeepSeek API 密钥")

    deepseek_api_base: str = Field(default="https://api.deepseek.com", description="DeepSeek API 基础 URL")

    deepseek_model: str = Field(default="deepseek-v4-flash", description="DeepSeek 模型名称")

    # ==================== Groq 配置 ====================
    groq_api_key: str = Field(default="", description="Groq API 密钥")

    groq_api_base: str = Field(default="https://api.groq.com", description="Groq API 基础 URL")

    groq_model: str = Field(default="llama-3.3-70b-versatile", description="Groq 模型名称")

    # ==================== 百度千帆 配置 ====================
    baidu_qianfan_api_key: str = Field(default="", description="百度千帆 API 密钥")

    baidu_qianfan_api_base: str = Field(default="https://qianfan.baidubce.com/v2", description="百度千帆 API 基础 URL")

    baidu_qianfan_model: str = Field(default="ernie-3.5-8k", description="百度千帆模型名称")

    # ==================== Tavily 搜索配置 ====================
    tavily_api_key: str = Field(default="", description="Tavily 搜索 API 密钥")

    tavily_max_results: int = Field(default=5, ge=1, le=20, description="Tavily 最大返回结果数")

    # ==================== 高德地图配置 ====================
    amap_key: str = Field(default="", description="高德地图 API 密钥")

    # ==================== 会话 Token 硬预算（COST-01）====================
    # 会话（thread/session）级累计 token 硬熔断上限：执行循环每轮 LLM 调用后检查
    # （会话已落库累计 + 当前请求增量），超限发布 budget_exceeded 事件并优雅终止。
    # 数据源为会话已落库消息 token_count 汇总 + 当前请求 TokenUsageCallbackHandler 增量；
    # 设为 0 表示不启用会话级熔断。
    session_token_budget: int = Field(
        default=2_000_000, ge=0, description="会话级 Token 硬预算上限（累计输入+输出+推理），0=不启用熔断"
    )

    # 唯一权威默认能力表（ARCH-06 双源合并）：全部 Agent 类型的默认 capability
    # 统一在此维护，agent_hub/config.py 导入同一对象引用，
    # CapabilityRegistry.get_default_capabilities fallback 亦读本字段。
    # 同步断言由 Django_xm.apps.ai_engine.tests.test_capability_defaults 保证。
    AGENT_CAPABILITIES_DEFAULT: dict = Field(
        default={
            "base": ["context_management", "tool_injection", "guardrails", "rate_limit"],
            "rag": ["context_management", "tool_injection"],
            "safe_rag": ["context_management", "tool_injection", "guardrails"],
            "deep_research": ["context_management", "tool_injection", "rate_limit"],
        },
        description="各 Agent 类型的默认能力列表（唯一权威定义，禁止在其他模块重复硬编码）",
    )

    # ==================== RAG / Embedding 配置 ====================
    embedding_model: str = Field(default="text-embedding-3-small", description="Embedding 模型名称")

    embedding_batch_size: int = Field(default=100, ge=1, le=1000, description="Embedding 批处理大小")

    local_embedding_model: str = Field(
        default="BAAI/bge-small-zh-v1.5", description="本地兜底 Embedding 模型（HuggingFace，无 API 消耗）"
    )

    # ==================== Ollama 配置 ====================
    ollama_base_url: str = Field(default="http://localhost:11435", description="Ollama 服务地址（默认本地 11435）")

    ollama_model: str = Field(default="qwen3:8b", description="Ollama 默认 chat 模型（需先 ollama pull <model>）")

    ollama_embedding_model: str = Field(default="bge-m3", description="Ollama 默认 embedding 模型（推荐 bge-m3）")

    # ==================== LLM 速率限制 ====================
    # 多 Agent 并行（主 agent + N 子代理）共享 LLM 出口，限速按 provider 区分：
    # 本地 provider（ollama，无 API 限额）默认不限速；外部 API 各自限速；
    # 特定 provider 可用 overrides 精确覆盖（如 Groq 免费档 30 RPM → 0.5 rps）。
    rate_limit_rps: float = Field(
        default=1.0,
        description="外部 API provider 默认速率限制（requests/second）；按 provider 独立计数而非全局共享",
    )

    rate_limit_bucket: int = Field(
        default=60,
        ge=1,
        description="令牌桶容量（允许的突发请求数）",
    )

    rate_limit_unlimited_providers: list[str] = Field(
        default_factory=lambda: ["ollama"],
        description="不限速的 provider 列表（本地推理服务，无 API 限额）",
    )

    rate_limit_rps_overrides: dict[str, float] = Field(
        default_factory=dict,
        description='按 provider 覆盖速率限制，如 {"groq": 0.5}；优先级高于 rate_limit_rps 默认值',
    )

    chunk_size: int = Field(default=1000, ge=100, le=10000, description="文本分块大小(字符)")

    chunk_overlap: int = Field(default=200, ge=0, le=1000, description="分块重叠大小(字符)")

    vector_store_type: str = Field(
        default="pgvector",
        description="向量库类型: pgvector/chroma/faiss/inmemory/milvus（推荐 pgvector，支持持久化和增量更新）",
    )

    vector_store_path: str = Field(default="data/indexes", description="向量库存储路径")

    chroma_persist_directory: str = Field(default="data/chroma_db", description="Chroma 持久化目录")

    chroma_collection_name: str = Field(default="langchain_xm", description="Chroma 默认集合名称")

    retriever_search_type: str = Field(
        default="similarity", description="检索类型: similarity/mmr/similarity_score_threshold"
    )

    retriever_k: int = Field(default=4, ge=1, le=20, description="检索返回文档数")

    retriever_score_threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="相似度阈值")

    retriever_fetch_k: int = Field(default=20, ge=1, le=100, description="MMR 候选文档数")

    retriever_comprehensive_k: int = Field(default=6, ge=1, le=30, description="全局分析模式检索文档数")

    retriever_use_multi_query: bool = Field(default=True, description="全局分析模式是否启用 MultiQuery 扩展召回")

    retriever_use_reranker: bool = Field(
        default=False,
        description=(
            "检索器是否启用 FlashRank 重排序。实测（30 题 Redis 语料）：开启后 Recall@6 "
            "1.000→0.967、MRR 0.833→0.783，P50 14ms→2084ms（英文模型）——当前语料下负收益，"
            "默认关闭；换更难语料复测后再决定"
        ),
    )

    retriever_intent_classification_enabled: bool = Field(default=True, description="是否启用查询意图自动分类")

    retriever_map_reduce_batch_size: int = Field(default=4, ge=2, le=10, description="Map-Reduce 每批文档数")

    # ==================== Unified RAG Pipeline 配置 ====================
    rag_score_threshold: float = Field(
        default=0.35,
        ge=0.0,
        le=1.0,
        description=(
            "RAG 召回阶段相似度阈值（精排之前）。工业口径为「宽召回 + 精排」："
            "中文 bge-m3 相关片段常在 0.35~0.65，text-embedding-3-small 常在 0.30~0.55，"
            "0.7 相当于要求近乎同义改写（系统性漏召回）。需用 evaluate_rag --score-thresholds 复验"
        ),
    )

    rag_degraded_threshold: float = Field(
        default=0.25, ge=0.0, le=1.0, description="RAG Pipeline 降级检索相似度阈值（Level 1 放宽召回）"
    )

    rag_initial_k: int = Field(default=6, ge=1, le=50, description="RAG Pipeline 初始检索返回文档数")

    rag_degraded_k: int = Field(
        default=30,
        ge=1,
        le=100,
        description=(
            "RAG Pipeline Level 1 降级检索候选数。不得小于 rag_recall_k，"
            "否则「扩展召回」反而比正常路径窗口更小"
        ),
    )

    rag_recall_k: int = Field(
        default=30,
        ge=1,
        le=200,
        description=(
            "RAG Pipeline 向量召回候选窗口（精排前）。启用 reranker 时向量检索先取 "
            "rag_recall_k 条候选，再由 FlashRank 精排到 min(k, rag_rerank_top_n) 条；"
            "不启用 reranker 时不生效（召回窗口等于调用方 k）"
        ),
    )

    rag_rerank_top_n: int = Field(
        default=6,
        ge=1,
        le=50,
        description="RAG Pipeline FlashRank 重排序保留数上限（实际取 min(k, 本值)）",
    )

    rag_rerank_model: str = Field(
        default="ms-marco-MiniLM-L-12-v2",
        description=(
            "FlashRank cross-encoder 模型标识。实测多语种 ms-marco-MultiBERT-L-12 在本项目中文语料上"
            "未见优于英文 MiniLM（Recall 0.933 vs 0.967，P50 3584ms vs 2084ms），默认回退英文模型；"
            "切换前需用评测对比"
        ),
    )

    rag_rerank_score_threshold: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "精排后 relevance_score 过滤阈值（0=不过滤）。不同 reranker 的分数校准不同，"
            "启用前需用评测确定（建议区间 0.05~0.2）"
        ),
    )

    rag_multi_kb_rerank_enabled: bool = Field(
        default=True, description="跨库检索在 RRF 融合后是否统一精排一次（1× 成本）"
    )

    # ==================== Hybrid（dense + sparse）检索 ====================
    rag_hybrid_enabled: bool = Field(
        default=False,
        description=(
            "是否启用混合召回（向量 + BM25，RRF 融合）。实测（DuRetrieval 100 题 / 2425 篇 / bge-m3）："
            "dense MRR 0.971，hybrid 最优 0.963（0.9/0.1 权重），四组权重均未超过纯向量，"
            "且 P50 +8ms；精确匹配子集（含数字/英文，27 题）亦无增益。当前默认关闭；"
            "领域术语/编号密集场景可开启复测"
        ),
    )

    rag_hybrid_dense_weight: float = Field(default=0.7, ge=0.0, le=1.0, description="Hybrid RRF 融合中的向量权重")

    rag_hybrid_sparse_weight: float = Field(default=0.3, ge=0.0, le=1.0, description="Hybrid RRF 融合中的 BM25 权重")

    # ==================== 质量增强（Phase 2，开发阶段效果优先）====================
    rag_multi_query_enabled: bool = Field(
        default=False,
        description=(
            "是否把 MultiQuery 改写作为额外召回路（原 query 始终保留；与 HyDE 并行）。"
            "实测（30 题 Redis 语料）：MRR 0.833→0.869（+4%），但 P50 从 14ms 升到 20.4s"
            "（HyDE+MQ 两次 LLM），当前默认关闭；难数据集复测后再决定"
        ),
    )

    rag_multi_query_count: int = Field(
        default=3, ge=1, le=8, description="MultiQuery 改写变体数（仅影响一路 LLM 输出，不增加 LLM 调用次数）"
    )

    rag_query_expansion_timeout: float = Field(
        default=20.0,
        ge=1.0,
        le=120.0,
        description="HyDE/MQ 查询扩展的等待超时（秒）；超时后仅用已生成的路，不阻断检索",
    )

    rag_context_window: int = Field(
        default=1,
        ge=0,
        le=3,
        description="邻块扩展窗口（chunk_index±N，0=关闭）。扩展块允许同文件多条，用于 small-to-big 上下文",
    )

    rag_max_chunks_per_source: int = Field(
        default=2, ge=1, le=10, description="召回去冗余：同一源文件最多保留的 chunk 数（邻块扩展不计入）"
    )

    rag_dedupe_similarity: float = Field(
        default=0.85, ge=0.0, le=1.0, description="召回去冗余的相似度阈值（embedding 余弦 / 2-gram Jaccard 共用）"
    )

    rag_dedupe_mode: str = Field(
        default="embedding",
        description=(
            "召回去冗余实现: embedding（语义向量余弦，使用前端设置的 Embedding provider，"
            "当前 ollama bge-m3）/ gram（2-gram Jaccard，零 IO 兑底）。embedding 不可用时自动回退 gram"
        ),
    )

    rag_doc_max_tokens: int = Field(
        default=800, ge=50, le=4000, description="单 chunk 上下文 token 上限（超出截断并注明）"
    )

    rag_context_max_tokens: int = Field(
        default=3000, ge=200, le=32000, description="上下文总 token 预算（超预算整块丢弃并注明）"
    )

    rag_citation_verify_enabled: bool = Field(
        default=True,
        description="是否对答案引用做真实性校验（LLM Judge；只移除不被支持的 [n]，不改写正文）",
    )

    rag_citation_verify_model_mode: str = Field(
        default="helper",
        description=(
            "引用校验使用的模型: helper（辅助轻量模型优先，主模型兜底，默认）/ "
            "main（主模型，如 deepseek-flash，效果优先）"
        ),
    )

    rag_citation_verify_max_sentences: int = Field(
        default=8, ge=1, le=20, description="单次引用校验的句子上限（控制 LLM 成本）"
    )

    rag_attachment_ttl_days: int = Field(
        default=7, ge=1, le=365, description="孤儿会话附件索引清理阈值（天）"
    )

    rag_tool_max_context_tokens: int = Field(
        default=2000, ge=200, le=8000, description="Agent/学习/附件检索结果的上下文 token 预算"
    )
    rag_tool_include_sources: bool = Field(
        default=True, description="工具是否向编排层返回结构化 sources（前端工具卡片展示）"
    )
    rag_attachment_collection_prefix: str = Field(
        default="attach_", description="会话附件 collection 名前缀"
    )

    rag_agent_cache_ttl: int = Field(
        default=300,
        ge=0,
        le=3600,
        description=(
            "Agent/学习/附件检索结果缓存 TTL（秒，0=关闭）。同一 scope 下相同 query+k+intent "
            "直接复用文档列表，跳过 embedding 与向量查询；索引写入/删除时按 collection 失效"
        ),
    )

    rag_intent_classification_enabled: bool = Field(
        default=True,
        description=(
            "是否开启检索意图自动分类（auto）：命中总结/概述/整篇/有哪些等词时走 comprehensive "
            "（放大召回数 + 放宽上下文预算），否则 precise。工具显式传 intent 时以显式值为准"
        ),
    )

    analytics_retrieval_sample_rate: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description=(
            "RAG 检索埋点采样率（1.0=全采样，0.0=关闭）。埋点经 Celery 异步落库，"
            "用于观测命中率/空结果率/降级率/耗时，不影响检索主链"
        ),
    )

    rag_fulltext_token_threshold: int = Field(
        default=5000, ge=0, le=100000, description="RAG Pipeline 全文注入 token 阈值"
    )

    rag_rrf_constant: int = Field(default=60, ge=1, le=200, description="RAG Pipeline RRF 融合常数")

    # ==================== Checkpointer 配置 ====================
    checkpointer_backend: str = Field(default="postgres", description="Checkpointer 后端: sqlite/memory/postgres")

    checkpointer_redis_cache_enabled: bool = Field(
        default=False, description="是否启用 Checkpointer Redis 热缓存（装饰器模式，PG 写优先）"
    )

    checkpointer_redis_ttl: int = Field(
        default=1800, ge=60, le=86400, description="Checkpointer Redis 缓存 TTL（秒）"
    )

    checkpointer_redis_lock_ttl: int = Field(
        default=5, ge=1, le=60, description="Checkpointer 回源限流锁 TTL（秒）"
    )

    # ==================== Store 配置 ====================
    store_backend: str = Field(default="postgres", description="Store 后端: memory/postgres")

    # ==================== Agent Cache 配置 ====================
    agent_cache_enabled: bool = Field(default=False, description="是否自动注入 Agent 级别缓存（InMemoryCache）")

    # ==================== Guardrails 配置 ====================
    guardrails_enabled: bool = Field(default=False, description="是否全局启用 Guardrails Middleware")

    guardrails_strict_mode: bool = Field(default=False, description="Guardrails 严格模式（验证失败直接抛异常）")

    guardrails_enable_pii: bool = Field(default=False, description="是否启用 PII 检测与脱敏")

    guardrails_max_message_count: int = Field(default=100, ge=1, description="Guardrails 最大消息数量限制")

    # ==================== LangSmith 配置 ====================
    langsmith_api_key: str = Field(default="", description="LangSmith API 密钥")

    langsmith_project: str = Field(default="langchain_xm", description="LangSmith 项目名称")

    langsmith_endpoint: str = Field(default="https://api.smith.langchain.com", description="LangSmith API 端点")

    langsmith_tracing: bool = Field(default=False, description="是否启用 LangSmith 追踪")

    # ==================== 工具调用使用防护配置 ====================
    # 借鉴 Cloud Code / Claude Code 的 PreToolUse 内容去重、PostToolUse
    # 配额追踪等机制；避免粗暴"3 次就强制终止"。
    # ARCH-12 限流双轨收敛：agent 工具调用频率限制统一由
    # guardrails.RateLimitMiddleware 承担（阈值单源维护于
    # capabilities.builtin.RateLimitCapability），此处不再保留限流阈值键。
    tool_usage_dedup_window_seconds: int = Field(
        default=30,
        ge=1,
        le=600,
        description="文件写入去重窗口（秒）。同一 (thread, path) 在此窗口内若 content_hash 相同，"
        "工具返回无变更响应且不计调用次数。",
    )
    tool_usage_dedup_cache_size: int = Field(default=1000, ge=10, le=100000, description="文件写入去重 LRU 缓存容量。")
    tool_usage_same_path_max: int = Field(
        default=6, ge=1, le=100, description="同一 path 在去重窗口内最多允许的写入次数（含去重命中）。"
    )
    tool_usage_same_path_diff_ratio: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="同 path 连续多次写入的实质增量判定阈值：增量占比 < 此值视为无意义重写（被打磨循环）。",
    )
    tool_usage_blocked_consecutive_max: int = Field(
        default=2, ge=1, le=10, description="连续 blocked 多少次后交由 termination_judge 走 LOOP_DETECTED 流程。"
    )
    # 通用资源防护配置（适用于所有工具，不仅 fs_write_file）
    tool_usage_general_dedup_enabled: bool = Field(
        default=True, description="是否启用通用 dedup + 通用资源循环检测。关闭后仅 fs_write_file 专用循环检测仍生效。"
    )
    tool_usage_same_resource_max: int = Field(
        default=6,
        ge=1,
        le=100,
        description="同一资源在去重窗口内累计调用次数超过此值且最近 3 次 payload_hash 全部相同，"
        "判定为无进展循环并 BLOCK。适用于所有工具（按 tool_fingerprint 注册表）。",
    )

    # ==================== LLM 熔断分级配置（安全修复 2.6 → 熔断分级策略）====================
    # 原 llm_fallback.py.LazyFallbackChatModel 的硬编码字面量提为配置项。
    # 分级策略设计理由（替代旧"任意错误连续 2 次即熔断"的单档策略）：
    # 1. agent 长任务场景下单次 429/超时（瞬时错误）很常见，旧策略单次即熔断，
    #    导致冷却期内全部请求静默降级到更弱的 fallback 模型，任务质量中途跳水
    #    且用户无感知——瞬时错误须连续多次确认（默认 5 次）才熔断，期间任一次
    #    成功即清零计数；
    # 2. 永久错误（认证/权限/余额类，复用 LazyFallbackChatModel._is_permanent_error
    #    的单一分类来源）重试无意义，仍立即熔断，避免反复撞墙拖慢失败终态；
    # 3. half_open 单次探测成功即恢复过脆（限流抖动期侥幸成功会立刻再被打回）——
    #    要求连续 2 次探测成功才恢复，探测总次数封顶 3 次，探测期任一失败
    #    立即重新熔断并重置冷却。
    llm_circuit_transient_threshold: int = Field(
        default=5,
        ge=1,
        le=100,
        description=(
            "主模型瞬时错误（429/超时/连接失败等可恢复错误）连续失败多少次后熔断（转 OPEN）；任一次成功清零计数"
        ),
    )

    llm_circuit_half_open_max_probes: int = Field(
        default=3,
        ge=1,
        le=100,
        description="half_open 态最多允许的主模型探测次数，耗尽仍未达成连续成功则重新熔断（转 OPEN 并重置冷却）",
    )

    llm_circuit_half_open_success_threshold: int = Field(
        default=2,
        ge=1,
        le=100,
        description="half_open 态连续探测成功多少次后恢复（转 CLOSED）",
    )

    llm_circuit_breaker_cooldown: float = Field(
        default=30.0,
        ge=0.0,
        description="熔断冷却时长（秒），冷却期满转 half_open 试探主模型",
    )

    # ==================== MCP 连接配置（安全修复 2.7）====================
    # 原 mcp/__init__.py.get_mcp_tools 的硬编码超时（stdio 15s / 远程 50s）
    # 提为配置项，默认值与原字面量一致（行为不变，仅可运维调整）。
    mcp_tool_fetch_timeout_stdio: int = Field(
        default=15,
        ge=1,
        le=300,
        description="MCP stdio 传输单 server 工具拉取超时（秒，本地子进程建连+枚举工具）",
    )

    mcp_tool_fetch_timeout_remote: int = Field(
        default=50,
        ge=1,
        le=300,
        description="MCP 远程传输（sse/http/websocket）单 server 工具拉取超时（秒）",
    )

    def get_openai_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {
            "api_key": self.openai_api_key,
            "base_url": self.openai_api_base,
            "model": self.openai_model,
            "temperature": self.openai_temperature,
        }
        if self.openai_max_tokens is not None:
            config["max_tokens"] = self.openai_max_tokens
        return config


# ==================== 单例管理 ====================

_settings_instance: Settings | None = None
_settings_lock = threading.Lock()


def get_settings() -> Settings:
    global _settings_instance  # noqa: PLW0603 - 模块级单例惰性初始化（标准双检锁模式）
    if _settings_instance is None:
        with _settings_lock:
            if _settings_instance is None:
                _settings_instance = Settings()
    return _settings_instance


settings = get_settings()


# ==================== 模型注册表 ====================

MODEL_REGISTRY: dict[str, dict[str, Any]] = {
    "openai": {
        "provider": "openai",
        "label": "OpenAI",
        "icon": "🔵",
        "models": [
            {"name": "gpt-4o", "capabilities": ["tool_calling", "streaming"]},
            {"name": "gpt-4o-mini", "capabilities": ["tool_calling", "streaming"]},
            {"name": "gpt-4-turbo", "capabilities": ["tool_calling", "streaming"]},
        ],
        "default_model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
        "api_key_attr": "openai_api_key",
        "base_url_attr": "openai_api_base",
        "model_attr": "openai_model",
        "special_params": {},
        "presets": {
            "default": {
                "model_name": "gpt-4o",
                "model_provider": "openai",
                "temperature": 0.7,
                "description": "默认模型，平衡性能和成本",
            },
            "fast": {
                "model_name": "gpt-4o-mini",
                "model_provider": "openai",
                "temperature": 0.7,
                "description": "快速模型，适合简单任务",
            },
            "precise": {
                "model_name": "gpt-4o",
                "model_provider": "openai",
                "temperature": 0.3,
                "description": "精确模型，适合需要准确性的任务",
            },
            "creative": {
                "model_name": "gpt-4o",
                "model_provider": "openai",
                "temperature": 1.0,
                "description": "创意模型，适合需要创造性的任务",
            },
        },
    },
    "deepseek": {
        "provider": "deepseek",
        "label": "DeepSeek",
        "icon": "🤖",
        "models": [
            {"name": "deepseek-v4-flash", "capabilities": ["tool_calling", "deep_thinking", "streaming"]},
            {"name": "deepseek-v4-pro", "capabilities": ["tool_calling", "deep_thinking", "streaming"]},
        ],
        "default_model": "deepseek-v4-flash",
        "api_key_env": "DEEPSEEK_API_KEY",
        "api_key_attr": "deepseek_api_key",
        "base_url_attr": "deepseek_api_base",
        "model_attr": "deepseek_model",
        "special_params": {
            "thinking": {
                "type": "toggle",
                "label": "思考模式",
                "description": "启用 DeepSeek 思考模式（深度思考模式下自动启用，其他模式下默认关闭）",
                "default": False,
                "model_kwarg": "thinking",
                "pass_mode": "extra_body",
                "enabled_value": {"type": "enabled"},
                "disabled_value": {"type": "disabled"},
            },
            "reasoning_effort": {
                "type": "select",
                "label": "思考强度",
                "description": "推理努力程度（high: 适合大多数场景, max: 适合复杂推理任务）",
                "options": ["high", "max"],
                "default": "high",
                "model_kwarg": "reasoning_effort",
                "pass_mode": "top_level",
            },
        },
        "presets": {},
    },
    "groq": {
        "provider": "groq",
        "label": "Groq",
        "icon": "⚡",
        "models": [
            {"name": "llama-3.3-70b-versatile", "capabilities": ["tool_calling", "streaming"]},
        ],
        "default_model": "llama-3.3-70b-versatile",
        "api_key_env": "GROQ_API_KEY",
        "api_key_attr": "groq_api_key",
        "base_url_attr": None,
        "model_attr": "groq_model",
        "special_params": {},
        "presets": {},
    },
    "baidu_qianfan": {
        "provider": "openai",
        "label": "百度千帆",
        "icon": "🟠",
        "models": [
            {"name": "ernie-3.5-8k", "capabilities": ["tool_calling", "streaming"]},
        ],
        "default_model": "ernie-3.5-8k",
        "api_key_env": "BAIDU_QIANFAN_API_KEY",
        "api_key_attr": "baidu_qianfan_api_key",
        "base_url_attr": "baidu_qianfan_api_base",
        "model_attr": "baidu_qianfan_model",
        "special_params": {},
        "presets": {},
    },
    "anthropic": {
        "provider": "anthropic",
        "label": "Anthropic",
        "icon": "🟣",
        "models": [
            {"name": "claude-sonnet-4-20250514", "capabilities": ["tool_calling", "streaming"]},
            {"name": "claude-3-5-haiku-20241022", "capabilities": ["tool_calling", "streaming"]},
        ],
        "default_model": "claude-sonnet-4-20250514",
        "api_key_env": "ANTHROPIC_API_KEY",
        "api_key_attr": "anthropic_api_key",
        "base_url_attr": None,
        "model_attr": None,
        "special_params": {},
        "presets": {
            "anthropic_default": {
                "model_name": "claude-sonnet-4-20250514",
                "model_provider": "anthropic",
                "temperature": 0.7,
                "description": "Anthropic Claude Sonnet 4，平衡性能和成本",
            },
            "anthropic_fast": {
                "model_name": "claude-3-5-haiku-20241022",
                "model_provider": "anthropic",
                "temperature": 0.7,
                "description": "Anthropic Claude Haiku，快速响应",
            },
            "anthropic_precise": {
                "model_name": "claude-sonnet-4-20250514",
                "model_provider": "anthropic",
                "temperature": 0.3,
                "description": "Anthropic Claude Sonnet 4，精确模式",
            },
        },
    },
    "ollama": {
        "provider": "ollama",
        "label": "Ollama 本地",
        "icon": "🦙",
        "models": [
            {"name": "qwen3:8b", "capabilities": ["tool_calling", "streaming", "deep_thinking"]},
            {"name": "qwen3.5:4b", "capabilities": ["tool_calling", "streaming", "deep_thinking"]},
            {"name": "hermes3:8b", "capabilities": ["tool_calling", "streaming"]},
        ],
        "default_model": "qwen3:8b",
        # Ollama 无 api_key，标记 None 触发"本地可用"分支
        "api_key_env": None,
        "api_key_attr": None,
        "base_url_attr": "ollama_base_url",
        "model_attr": "ollama_model",
        "special_params": {},
        "presets": {
            "ollama_default": {
                "model_name": "qwen3:8b",
                "model_provider": "ollama",
                "temperature": 0.7,
                "description": "Ollama 本地 qwen3 8B，无需 API key",
            },
            "ollama_qwen35": {
                "model_name": "qwen3.5:4b",
                "model_provider": "ollama",
                "temperature": 0.7,
                "description": "Ollama 本地 qwen3.5 4B",
            },
            "ollama_hermes": {
                "model_name": "hermes3:8b",
                "model_provider": "ollama",
                "temperature": 0.7,
                "description": "Ollama 本地 hermes3 8B",
            },
        },
    },
}

HELPER_MODEL_PRIORITY: list[dict[str, str]] = [
    {"provider": "deepseek", "model": "deepseek-v4-flash", "reason": "性价比最高"},
    {"provider": "groq", "model": "llama-3.3-70b-versatile", "reason": "免费快速"},
    {"provider": "openai", "model": "gpt-4o-mini", "reason": "OpenAI 便宜模型"},
    {"provider": "baidu_qianfan", "model": "ernie-3.5-8k", "reason": "百度便宜模型"},
    {"provider": "ollama", "model": "qwen3:8b", "reason": "本地 Ollama 兜底（无需 API key）"},
]


def get_model_presets() -> dict[str, dict[str, Any]]:
    """从数据库汇总所有预设配置，返回扁平化的 preset_name -> config 映射"""
    from Django_xm.apps.ai_engine.services.registry_service import get_model_registry

    presets: dict[str, dict[str, Any]] = {}
    for _provider_id, cfg in get_model_registry().items():
        for preset_name, preset_cfg in cfg.get("presets", {}).items():
            presets[preset_name] = preset_cfg
    return presets


def get_available_providers() -> list[dict[str, Any]]:
    """从数据库获取所有 Provider 列表（包含不可用的，available 字段标记可用性）"""
    from Django_xm.apps.ai_engine.services.registry_service import get_model_registry, is_provider_available

    result = []
    for key, cfg in get_model_registry().items():
        available = is_provider_available(key)
        models = cfg.get("models", [])
        # 将模型列表统一为包含 capabilities 的字典列表格式
        normalized_models = []
        for model_cfg in models:
            if isinstance(model_cfg, dict):
                normalized_models.append(model_cfg)
            else:
                normalized_models.append({"name": model_cfg, "capabilities": []})
        result.append(
            {
                "id": key,
                "provider": cfg["provider"],
                "label": cfg["label"],
                "icon": cfg["icon"],
                "models": normalized_models,
                "default_model": cfg["default_model"],
                "available": available,
                "special_params": cfg.get("special_params", {}),
            }
        )
    return result
