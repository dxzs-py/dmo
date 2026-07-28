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

    compression_enabled: bool = Field(
        default=True,
        description="是否启用上下文自动压缩"
    )

    compression_strategy: str = Field(
        default="hybrid",
        description="压缩策略: summary/sliding_window/hybrid"
    )

    compression_threshold_ratio: float = Field(
        default=0.8,
        ge=0.5,
        le=0.95,
        description="压缩触发阈值（占模型 token 限制的比例）"
    )

    compression_keep_recent: int = Field(
        default=6,
        ge=2,
        le=50,
        description="压缩时保留的最近消息数"
    )

    compression_summary_max_length: int = Field(
        default=1500,
        ge=200,
        le=5000,
        description="压缩摘要最大长度"
    )

    kg_enabled: bool = Field(
        default=True,
        description="是否启用知识图谱上下文管理"
    )

    kg_max_hops: int = Field(
        default=2,
        ge=1,
        le=5,
        description="知识图谱检索最大跳数"
    )

    kg_max_entities: int = Field(
        default=20,
        ge=5,
        le=100,
        description="知识图谱检索最大实体数"
    )

    cross_session_enabled: bool = Field(
        default=True,
        description="是否启用跨会话上下文复用"
    )

    cross_session_max_context_length: int = Field(
        default=2000,
        ge=500,
        le=10000,
        description="跨会话上下文最大长度"
    )

    long_term_tags: str = Field(
        default="system,preference,decision",
        description="长期记忆标记关键词（逗号分隔）"
    )

    budget_templates: str = Field(
        default="",
        description="自定义 Token 预算模板（JSON 字符串，从环境变量 CONTEXT_BUDGET_TEMPLATES 加载）。"
        "格式: {\"template_name\": {\"system\": 0.15, \"memory\": 0.10, \"tools\": 0.10, \"history\": 0.50, \"state\": 0.05, \"query\": 0.10}}"
    )

    cross_session_max_entries: int = Field(
        default=20,
        ge=5,
        le=100,
        description="跨会话记忆最大条目数，超限按 LRU 淘汰"
    )

    compression_level_2_threshold: float = Field(
        default=0.5,
        ge=0.3,
        le=0.7,
        description="Level 2 压缩触发阈值（占模型 token 限制的比例）"
    )

    compression_level_3_threshold: float = Field(
        default=0.75,
        ge=0.6,
        le=0.85,
        description="Level 3 压缩触发阈值（占模型 token 限制的比例）"
    )

    compression_level_4_threshold: float = Field(
        default=0.9,
        ge=0.8,
        le=0.95,
        description="Level 4 压缩触发阈值（占模型 token 限制的比例）"
    )

    compression_lightweight_model: str = Field(
        default="",
        description="压缩摘要使用的轻量模型标识，为空时自动选择辅助模型"
    )

    termination_goal_complete_window: int = Field(
        default=3,
        ge=1,
        le=10,
        description="目标完成检测窗口大小（最近 N 轮）"
    )

    termination_info_gain_threshold: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="信息增益衰减阈值"
    )

    termination_info_gain_window: int = Field(
        default=4,
        ge=2,
        le=10,
        description="信息增益衰减检测窗口大小"
    )

    token_budget_limit: int = Field(
        default=500000,
        ge=50000,
        description="单次 Agent 调用的累计 token 预算上限"
    )

    # ── 循环检测阈值（渐进式，Trae 动态轮次思想） ──
    loop_same_call_warn_threshold: int = Field(
        default=3,
        ge=2,
        le=10,
        description="连续相同调用警告阈值（WARN），注入警告消息让Agent自纠正"
    )
    loop_same_call_throttle_threshold: int = Field(
        default=5,
        ge=3,
        le=15,
        description="连续相同调用限流阈值（THROTTLE），注入强警告引导Agent停止"
    )
    loop_same_call_terminate_threshold: int = Field(
        default=8,
        ge=5,
        le=20,
        description="连续相同调用终止阈值（TERMINATE），强制终止作为安全网"
    )
    loop_window_size: int = Field(
        default=10,
        ge=4,
        le=30,
        description="循环检测滑动窗口大小"
    )
    loop_diversity_warn_threshold: float = Field(
        default=0.25,
        ge=0.1,
        le=0.5,
        description="多样性警告阈值（WARN）"
    )
    loop_diversity_terminate_threshold: float = Field(
        default=0.15,
        ge=0.05,
        le=0.3,
        description="多样性终止阈值（TERMINATE）"
    )
    loop_args_diversity_threshold: float = Field(
        default=0.2,
        ge=0.05,
        le=0.5,
        description="参数多样性终止阈值，低于此值且工具多样性也低才终止"
    )
    loop_args_progressive_threshold: float = Field(
        default=0.5,
        ge=0.2,
        le=0.8,
        description="参数递进模式检测阈值，参数多样性高于此值视为合理重复"
    )
    loop_multi_call_safe_tools: str = Field(
        default="fs_write_file,fs_read_file,knowledge_base,shell_exec,agent_run,agent_create,skill_agent-browser,skill_baidu-search",
        description="多调用安全工具列表（逗号分隔，这些工具天然需要多次调用）"
    )


context_settings = ContextManagerSettings()
