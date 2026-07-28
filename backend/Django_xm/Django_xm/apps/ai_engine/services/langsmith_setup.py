"""LangSmith 追踪配置统一入口。

抽取自 ``apps/ai_engine/services/agent_factory.py`` 与
``apps/agent_hub/builders/base_builder.py`` 的重复实现（Task 14.1 / 14.2）。

环境变量命名遵循 LangChain 官方约定，统一使用 ``LANGCHAIN_*`` 前缀，
不再使用 ``LANGSMITH_*`` 旧前缀（Task 16.2）。

调用时机：
    - 由 ``AiEngineConfig.ready()`` 在 Django 启动时统一调用（Task 16.3）
    - 不再在模块顶层执行（避免 import 副作用与重复初始化）

归属说明（Task 15.6a）：
    本模块原位于 ``apps/core/services/langsmith_setup.py``，但其依赖
    ``ai_engine.config``，违反 ``core`` 不依赖业务 app 的分层约束。
    LangSmith 追踪是 AI 引擎的职责，故迁入 ``ai_engine/services/``。
"""

from __future__ import annotations

import logging
import os
from typing import Final

from Django_xm.apps.ai_engine.config import settings as app_cfg

logger = logging.getLogger(__name__)


# LangChain 官方环境变量名（统一使用 LANGCHAIN_* 前缀）
# 参考: https://docs.smith.langchain.com/observability/tutorials/setup
ENV_LANGCHAIN_API_KEY: Final[str] = "LANGCHAIN_API_KEY"
ENV_LANGCHAIN_TRACING_V2: Final[str] = "LANGCHAIN_TRACING_V2"
ENV_LANGCHAIN_PROJECT: Final[str] = "LANGCHAIN_PROJECT"
ENV_LANGCHAIN_ENDPOINT: Final[str] = "LANGCHAIN_ENDPOINT"


def _is_truthy(value: str) -> bool:
    """判断环境变量字符串是否为 truthy（true/1/yes）。"""
    return value.lower() in ("true", "1", "yes")


def configure_langsmith() -> None:
    """统一配置 LangSmith 追踪环境变量。

    配置优先级：
        1. ``ai_engine.settings.langsmith_tracing`` 为 True 时强制启用
        2. 环境变量 ``LANGCHAIN_API_KEY`` + ``LANGCHAIN_TRACING_V2=true`` 时启用
        3. 否则不启用

    当启用时，按以下顺序注入环境变量（仅当未设置时）：
        - ``LANGCHAIN_TRACING_V2=true``
        - ``LANGCHAIN_API_KEY``（优先用 settings，回退到环境变量）
        - ``LANGCHAIN_PROJECT``（来自 settings.langsmith_project）
        - ``LANGCHAIN_ENDPOINT``（来自 settings.langsmith_endpoint）

    本函数幂等，多次调用不会覆盖已设置的值（使用 ``os.environ.setdefault``）。
    """
    env_api_key = os.environ.get(ENV_LANGCHAIN_API_KEY, "")
    env_tracing = _is_truthy(os.environ.get(ENV_LANGCHAIN_TRACING_V2, ""))

    # 启用条件：settings 显式启用，或环境变量已配置 API Key + Tracing
    should_enable = bool(app_cfg.langsmith_tracing) or (bool(env_api_key) and env_tracing)

    if not should_enable:
        logger.debug(
            "LangSmith 追踪未启用 (settings.langsmith_tracing=%s, env_api_key_set=%s, env_tracing=%s)",
            app_cfg.langsmith_tracing, bool(env_api_key), env_tracing,
        )
        return

    # 注入环境变量（setdefault 不覆盖已有值）
    os.environ.setdefault(ENV_LANGCHAIN_TRACING_V2, "true")

    # API Key：优先 settings，回退环境变量
    if app_cfg.langsmith_api_key:
        os.environ.setdefault(ENV_LANGCHAIN_API_KEY, app_cfg.langsmith_api_key)
    elif env_api_key:
        os.environ.setdefault(ENV_LANGCHAIN_API_KEY, env_api_key)

    # Project 与 Endpoint：从 settings 注入
    if app_cfg.langsmith_project:
        os.environ.setdefault(ENV_LANGCHAIN_PROJECT, app_cfg.langsmith_project)
    if app_cfg.langsmith_endpoint:
        os.environ.setdefault(ENV_LANGCHAIN_ENDPOINT, app_cfg.langsmith_endpoint)

    # 关闭 LangChain debug/verbose，避免与 LangSmith tracing 冲突
    try:
        from langchain_core.globals import set_debug, set_verbose
        set_debug(False)
        set_verbose(False)
    except ImportError:
        logger.debug("langchain_core 未安装，跳过 debug/verbose 设置")

    logger.info(
        "LangSmith 追踪已启用 (project=%s, endpoint=%s)",
        app_cfg.langsmith_project, app_cfg.langsmith_endpoint,
    )
