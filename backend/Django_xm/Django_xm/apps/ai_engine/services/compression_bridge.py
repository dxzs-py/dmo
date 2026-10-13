"""
压缩/评估模型解析桥（环 1 依赖倒置）

背景（spec eliminate-priority-debts 任务 4.1 / agent_audit_arch §4.2）：
context_manager.services.compression 原在函数内 import llm_factory 构建
"轻量模型 → 辅助模型 → SystemConfig 默认 → 兜底" 压缩模型解析链，构成
context_manager → ai_engine 反向边。现解析链整体上移至引擎侧本模块，由
AiEngineConfig.ready() 注册进 context_manager.services.runtime_providers，
供 ContextCompressionEngine._get_compression_model / evaluate_summary_quality
以 provider 形式调用（解析逻辑与原内联实现逐级等价，日志文案保持一致）。
"""

import logging
from typing import Any

from Django_xm.apps.ai_engine.services.llm_factory import (
    get_chat_model,
    get_helper_model,
    get_model_string,
    get_system_default_chat_model,
)

logger = logging.getLogger(__name__)


def resolve_compression_model(lightweight_model: str | None = None) -> Any:
    """压缩/摘要模型解析链（原 compression._get_compression_model 内联逻辑迁移）。

    Args:
        lightweight_model: context_settings.compression_lightweight_model 配置值
            （由调用方传入，本模块不反向 import context_manager 配置）。

    Returns:
        解析后的模型实例。
    """
    # 1. 用户显式指定的轻量模型（如 "deepseek:deepseek-chat"）
    if lightweight_model:
        try:
            return get_chat_model(lightweight_model)
        except Exception:
            # 轻量模型创建失败时回退到下一个选项
            logger.debug("轻量模型 %s 创建失败，回退到辅助模型", lightweight_model)

    # 2. 辅助模型（带 fallback 包装，轻量任务优先）
    try:
        helper = get_helper_model()
        if helper is not None:
            return helper
    except Exception:
        # 辅助模型获取失败时回退到 SystemConfig 默认模型
        logger.debug("获取辅助模型失败，回退到 SystemConfig 默认模型")

    # 3. SystemConfig 默认模型
    try:
        system_default = get_system_default_chat_model()
        if system_default and system_default.get("provider_id"):
            return get_chat_model(
                model_provider=system_default["provider_id"],
                model_name=system_default.get("model_name"),
            )
    except Exception:
        # SystemConfig 默认模型获取失败时回退到最终兜底
        logger.debug("获取 SystemConfig 默认模型失败，回退到最终兜底")

    # 4. 最终兜底：默认 provider/model
    return get_chat_model(get_model_string())


def resolve_evaluation_model() -> Any:
    """摘要质量评估模型（原 evaluate_summary_quality 内联逻辑迁移）：辅助模型 → 默认模型。"""
    return get_helper_model() or get_chat_model()
