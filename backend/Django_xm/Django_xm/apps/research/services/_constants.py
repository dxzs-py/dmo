"""研究模块共享常量。

抽取自 ``apps/agent_hub/builders/deep_builder.py`` 与
``apps/research/services/adapter.py`` 的重复定义
（Task 14.4 + Task 18.2）。

单一来源：所有研究相关常量在此定义，避免两处定义不一致导致行为漂移。

spec 任务 4.3 环 3 破除时两常量移出本模块（消除 agent_hub → research
反向依赖边的常量源）：
- ``SANDBOX_ALLOWED_DIRS`` → ``Django_xm.common.sandbox.constants``（机制层，
  沙箱目录布局与业务 app 无关）；
- ``RETRIEVER_TOOL_NAME_PREFIXES`` → ``Django_xm.common.constants``（机制层，
  工具命名约定）。
"""

from __future__ import annotations

from typing import Final

# ============================================================================
# 工具名集合
# ============================================================================

# 网络搜索工具名集合（用于判断是否已加载搜索工具）
SEARCH_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "web_search",
        "tavily_search",
        "duckduckgo_search",
        "bing_search",
        "google_search",
        "serpapi_search",
        "searx_search",
        "brave_search",
    }
)
