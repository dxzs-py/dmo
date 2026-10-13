"""Agent 嵌套深度上限单源（C14 收敛）。

历史形态：``tools/langchain/agent_context.py`` 与
``ai_engine/subagent_runtime/runtime.py`` 各自维护"读 settings.AGENT_MAX_DEPTH
并回退默认值 3"的重复实现（且 ai_engine 禁止 import tools，无法直接复用）。
收敛后常量与判定函数唯一实现在本模块（common 包，业务 app 均可依赖）；
agent_context / runtime 保留原名字做兼容 re-export。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 默认最大子代理嵌套深度（0=主代理, 1=子代理, 2=孙代理, 3=曾孙代理）
DEFAULT_MAX_AGENT_DEPTH: int = 3


def get_max_agent_depth() -> int:
    """读取最大子代理嵌套深度（lazy 读 settings.AGENT_MAX_DEPTH，兼容非 Django 上下文）。

    Django settings 未配置/读取异常（如独立 unittest 上下文）时回退默认值，
    保证本模块可在无 Django 环境下被引用；回退留 debug 日志便于排查
    （配置读取失败会让过深嵌套通过，需要时可按日志追查）。
    """
    try:
        from django.conf import settings

        return int(getattr(settings, "AGENT_MAX_DEPTH", DEFAULT_MAX_AGENT_DEPTH))
    except Exception as e:
        logger.debug(f"读取 settings.AGENT_MAX_DEPTH 失败，回退默认值 {DEFAULT_MAX_AGENT_DEPTH}: {e}")
        return DEFAULT_MAX_AGENT_DEPTH
