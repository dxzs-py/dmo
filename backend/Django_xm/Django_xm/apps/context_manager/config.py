"""
上下文管理子应用配置

独立于 ai_engine.config，管理上下文管理相关的所有配置项。
支持从环境变量和 .env 文件加载。

使用方式:
    from Django_xm.apps.context_manager.config import context_settings
    threshold = context_settings.compression_threshold_ratio
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ContextManagerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    compression_enabled: bool = Field(default=True, description="是否启用上下文自动压缩")

    compression_strategy: str = Field(default="hybrid", description="压缩策略: summary/hybrid")

    # ── 压缩双路径阈值语义（两条路径并存，数值不同是有意分层，勿"统一"） ──
    #
    # 路径 A（自研压缩引擎，本项 = 0.8）：
    #   消费点：manager.py ContextManagementConfig.from_settings →
    #   CompressionConfig.token_threshold_ratio（services/compression.py），
    #   trigger_threshold = model_limit × 0.8。
    #   触发位置：chat 上下文工程链路 build_structured_context 的
    #   _check_token_budget 报告分区超限后 → _apply_compression_if_needed →
    #   ContextCompressionEngine.compress（预算驱动的被动兜底，只压 history）。
    #   带 evaluate_summary_quality 质量门禁：摘要不达标时降级为【原文关键片段】
    #   保留（CompressionResult.degraded → metadata → 用户可见提示）。
    # 路径 B（官方 SummarizationMiddleware，见 compression_level_2_threshold = 0.5）。
    #
    # 为何 0.8 > 0.5：本路径是上下文工程链路的最后一道兜底，仅在预算检查实际
    # 超限后才介入，晚触发可尽可能保留原始历史（有质量门禁兜底降级，不怕
    # 压坏）；官方路径是 agent 运行时每轮模型调用前的第一道防线，须提前触发
    # 为单轮内的工具调用与输出生长预留空间（见下一项注释）。
    compression_threshold_ratio: float = Field(
        default=0.8, ge=0.5, le=0.95, description="自研压缩引擎触发阈值（占模型 token 限制的比例，预算超限兜底路径）"
    )

    compression_keep_recent: int = Field(default=6, ge=2, le=50, description="压缩时保留的最近消息数")

    compression_summary_max_length: int = Field(default=1500, ge=200, le=5000, description="压缩摘要最大长度")

    kg_enabled: bool = Field(default=True, description="是否启用知识图谱上下文管理")

    kg_max_hops: int = Field(default=2, ge=1, le=5, description="知识图谱检索最大跳数")

    kg_max_entities: int = Field(default=20, ge=5, le=100, description="知识图谱检索最大实体数")

    cross_session_enabled: bool = Field(default=True, description="是否启用跨会话上下文复用")

    budget_templates: str = Field(
        default="",
        description="自定义 Token 预算模板（JSON 字符串，从环境变量 CONTEXT_BUDGET_TEMPLATES 加载）。"
        '格式: {"template_name": {"system_static": 0.10, "system_dynamic": 0.05, "memory": 0.10, '
        '"tools": 0.10, "history": 0.50, "state": 0.05, "query": 0.10}}；'
        "分区键必须与内置模板完全一致（含 system_static/system_dynamic 拆分键），比例总和须为 1.0",
    )

    cross_session_max_entries: int = Field(
        default=20, ge=5, le=100, description="跨会话记忆最大条目数，超限按 LRU 淘汰"
    )

    cross_session_context_window: int = Field(
        default=5, ge=1, le=50, description="跨会话上下文注入时参与相关性评分的最大会话记忆条数（4.2 窗口配置化）"
    )

    # 路径 B（官方 SummarizationMiddleware，本项 = 0.5）：
    #   消费点：middleware.py ContextManagerMiddleware.__init__ →
    #   _trigger_tokens = model_limit × 0.5 → SummarizationMiddleware
    #   trigger=("tokens", _trigger_tokens)，另有消息数硬限制兜底
    #   （{"messages": model_limit // 50}，middleware.py _build_summarizer）。
    #   触发位置：agent 图每轮模型调用前 before_model，按 memory_tier 分层后
    #   仅对短期消息做摘要替换（长期记忆原样保留，摘要 HumanMessage 置于
    #   最近消息之前）。
    # 命名说明："Level 2" 沿用自研体系压缩层级中"摘要"档位的语义，指压缩
    # 手段等级，而非第二道触发顺序——实际运行时官方路径（0.5）先于自研
    # 路径（0.8）触发，两者不共享状态、互不感知。
    compression_level_2_threshold: float = Field(
        default=0.5,
        ge=0.3,
        le=0.7,
        description="官方 SummarizationMiddleware 触发阈值（占模型 token 限制的比例，agent 运行时首道防线）",
    )

    compression_lightweight_model: str = Field(
        default="", description="压缩摘要使用的轻量模型标识，为空时自动选择辅助模型"
    )

    termination_goal_complete_window: int = Field(
        default=3, ge=1, le=10, description="目标完成检测窗口大小（最近 N 轮）"
    )

    termination_goal_complete_min_rounds: int = Field(
        default=6, ge=2, le=50,
        description="目标完成判定所需的最低 AI 消息轮数（round3 2.9 去硬编码；"
        "短于此轮数不做 goal_completed 判定，防止浅尝辄止误终止）",
    )

    termination_info_gain_threshold: float = Field(default=0.15, ge=0.0, le=1.0, description="信息增益衰减阈值")

    termination_info_gain_window: int = Field(default=4, ge=2, le=10, description="信息增益衰减检测窗口大小")

    termination_info_gain_consecutive_rounds: int = Field(
        default=6, ge=2, le=30,
        description="信息增益衰减 TERMINATE 所需的连续低增益轮数（round3 2.9："
        "短窗口低置信信号不足以下终止结论，需连续 N 轮低于阈值才允许终止，"
        "防止中文长任务被误杀）",
    )

    token_budget_limit: int = Field(default=500000, ge=50000, description="单次 Agent 调用的累计 token 预算上限")

    # ── 循环检测阈值（渐进式，Trae 动态轮次思想） ──
    loop_same_call_warn_threshold: int = Field(
        default=3, ge=2, le=10, description="连续相同调用警告阈值（WARN），注入警告消息让Agent自纠正"
    )
    loop_same_call_throttle_threshold: int = Field(
        default=5, ge=3, le=15, description="连续相同调用限流阈值（THROTTLE），注入强警告引导Agent停止"
    )
    loop_same_call_terminate_threshold: int = Field(
        default=8, ge=5, le=20, description="连续相同调用终止阈值（TERMINATE），强制终止作为安全网"
    )
    loop_window_size: int = Field(default=10, ge=4, le=30, description="循环检测滑动窗口大小")
    loop_diversity_warn_threshold: float = Field(default=0.25, ge=0.1, le=0.5, description="多样性警告阈值（WARN）")
    loop_diversity_terminate_threshold: float = Field(
        default=0.15, ge=0.05, le=0.3, description="多样性终止阈值（TERMINATE）"
    )
    loop_args_diversity_threshold: float = Field(
        default=0.2, ge=0.05, le=0.5, description="参数多样性终止阈值，低于此值且工具多样性也低才终止"
    )
    loop_args_progressive_threshold: float = Field(
        default=0.5, ge=0.2, le=0.8, description="参数递进模式检测阈值，参数多样性高于此值视为合理重复"
    )
    loop_multi_call_safe_tools: str = Field(
        default="fs_write_file,fs_read_file,knowledge_base,shell_exec,spawn_sub_agent,skill_agent-browser,skill_baidu-search",
        description="多调用安全工具列表（逗号分隔，这些工具天然需要多次调用）",
    )

    # ── deep_research 动态 prompt 上下文（opt-in） ──
    deep_dynamic_context_enabled: bool = Field(
        default=False,
        description="deep_research 动态 prompt 上下文开关，默认关闭保证行为不变；"
        "开启后 DeepAgentBuilder 默认 system_prompt 优先走 build_prompt_context 动态构建，"
        "构建失败自动回退静态 prompt",
    )


context_settings = ContextManagerSettings()
