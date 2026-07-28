"""研究模块共享常量。

抽取自 ``apps/agent_hub/builders/deep_builder.py`` 与
``apps/research/services/official_deep_agent.py`` 的重复定义
（Task 14.4 + Task 18.2）。

单一来源：所有研究相关工具名集合、沙箱目录等常量在此定义，
避免两处定义不一致导致行为漂移
（曾出现 ``_SANDBOX_ALLOWED_DIRS`` 在 deep_builder 与 official_deep_agent 不一致的 bug）。
"""

from __future__ import annotations

from typing import Final

# ============================================================================
# 工具名集合
# ============================================================================

# 网络搜索工具名集合（用于判断是否已加载搜索工具）
SEARCH_TOOL_NAMES: Final[frozenset[str]] = frozenset({
    'web_search',
    'tavily_search',
    'duckduckgo_search',
    'bing_search',
    'google_search',
    'serpapi_search',
    'searx_search',
    'brave_search',
})


# 知识库检索工具名前缀（用于判断是否已加载检索工具）
RETRIEVER_TOOL_NAME_PREFIXES: Final[tuple[str, ...]] = (
    'knowledge_base_',
    'knowledge_bases',
    'knowledge_retrieve',
)


# ============================================================================
# 沙箱目录配置
# ============================================================================

# Agent 工具区允许写入的子目录
# /sandbox/ 是 Agent 工具区（skills、MCP 工具、第三方依赖等），
# 只允许预定义的工具子目录写入，其他 /sandbox/ 路径一律拒绝，
# 引导 Agent 将研究产出写入 /notes/、/plans/、/reports/。
SANDBOX_ALLOWED_DIRS: Final[tuple[str, ...]] = (
    "/sandbox/skills/",
    "/sandbox/mcp/",
    "/sandbox/deps/",
    "/sandbox/tmp/",
    "/sandbox/artifacts/",  # 工具结果缓存（系统自动管理）
    "/sandbox/inherited/",   # 续研时继承的父任务文件，供 agent 读取参考
)
