from typing import Optional, List
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field
import logging

from Django_xm.apps.tools.errors import StandardToolResult, ToolStatus, TOOL_VERSION
from Django_xm.apps.tools.base import AsyncToolMixin, SafeConfigMixin

logger = logging.getLogger(__name__)

try:
    from langchain_tavily import TavilySearch
    USING_NEW_TAVILY = True
except ImportError:
    try:
        from langchain_community.tools.tavily_search import TavilySearchResults as TavilySearch
        USING_NEW_TAVILY = False
    except ImportError:
        TavilySearch = None
        USING_NEW_TAVILY = False


def get_tavily_api_key() -> Optional[str]:
    return SafeConfigMixin.get_config('tavily_api_key', env_key='TAVILY_API_KEY')


def get_tavily_max_results() -> int:
    val = SafeConfigMixin.get_config('tavily_max_results', default=5, env_key='TAVILY_MAX_RESULTS')
    return int(val)


def create_tavily_search_tool(
    max_results: Optional[int] = None,
    search_depth: str = "advanced",
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
):
    if TavilySearch is None:
        raise ValueError("Tavily 搜索工具未安装！请安装: pip install langchain-tavily")

    tavily_api_key = get_tavily_api_key()
    if not tavily_api_key:
        raise ValueError("Tavily API Key 未设置！请在环境变量或 .env 文件中设置 TAVILY_API_KEY")

    max_results = max_results or get_tavily_max_results()

    logger.info(f"🔍 创建 Tavily 搜索工具 (max_results={max_results}, depth={search_depth})")

    tool_kwargs = {
        "max_results": max_results,
        "api_key": tavily_api_key,
    }

    if USING_NEW_TAVILY:
        tool_kwargs["search_depth"] = search_depth
        if include_domains is not None:
            tool_kwargs["include_domains"] = include_domains
        if exclude_domains is not None:
            tool_kwargs["exclude_domains"] = exclude_domains
    else:
        tool_kwargs["search_depth"] = search_depth
        if include_domains is not None:
            tool_kwargs["include_domains"] = include_domains
        else:
            tool_kwargs["include_domains"] = []
        if exclude_domains is not None:
            tool_kwargs["exclude_domains"] = exclude_domains
        else:
            tool_kwargs["exclude_domains"] = []

    try:
        tool_instance = TavilySearch(**tool_kwargs)
        return tool_instance
    except Exception as e:
        logger.error(f"❌ 创建 Tavily 搜索工具失败: {e}")
        raise


class WebSearchInput(BaseModel):
    query: str = Field(description="搜索查询关键词")


class WebSearchTool(AsyncToolMixin, BaseTool):
    name: str = "web_search"
    version: str = TOOL_VERSION
    metadata: dict = {"tier": "extended", "visibility": "switch", "category": "web_search"}
    description: str = (
        "使用 Tavily 搜索引擎进行网络搜索，获取最新网络信息和事实性答案。"
        "适用场景：需要获取实时信息、查找新闻、验证事实、了解最新动态、搜索技术文档。"
        "不适用：数学计算、代码执行、本地文件操作、天气查询（应使用天气工具）。"
        "参数：query-搜索关键词（必填，尽量简洁精准）。"
        "边界：需要配置 TAVILY_API_KEY，搜索结果数量由系统配置决定。"
    )
    args_schema: type[BaseModel] = WebSearchInput

    def _run(self, query: str) -> str:
        try:
            tool = create_tavily_search_tool()
            results = tool.invoke(query)

            if not results:
                return StandardToolResult(
                    content="未找到相关结果",
                    status=ToolStatus.PARTIAL,
                    source="tavily",
                ).to_tool_message()

            if isinstance(results, str):
                return StandardToolResult(
                    content=results,
                    source="tavily",
                ).to_tool_message()

            if not isinstance(results, list):
                return StandardToolResult(
                    content=str(results),
                    source="tavily",
                ).to_tool_message()

            formatted_results = []
            for i, result in enumerate(results, 1):
                if isinstance(result, str):
                    formatted_results.append(f"{i}. {result}")
                    continue
                if not isinstance(result, dict):
                    formatted_results.append(f"{i}. {str(result)}")
                    continue
                title = result.get("title", "无标题")
                url = result.get("url", "")
                content = result.get("content", "")[:300]

                formatted_results.append(
                    f"{i}. {title}\n   URL: {url}\n   摘要: {content}..."
                )

            output = "搜索结果：\n\n" + "\n\n".join(formatted_results)
            logger.info(f"🔍 搜索完成，返回 {len(results)} 条结果")
            return StandardToolResult(
                content=output,
                metadata={"result_count": len(results)},
                source="tavily",
            ).to_tool_message()

        except Exception as e:
            error_msg = f"搜索失败: {str(e)}"
            logger.error(error_msg)
            return StandardToolResult(
                content=error_msg,
                status=ToolStatus.ERROR,
                source="tavily",
            ).to_tool_message()


web_search = WebSearchTool()


def get_web_search_tools():
    return [web_search]
