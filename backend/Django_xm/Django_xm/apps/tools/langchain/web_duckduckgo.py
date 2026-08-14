import logging

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

try:
    from langchain_community.tools import DuckDuckGoSearchResults as DuckDuckGoSearch

    HAS_DUCKDUCKGO_TOOL = True
    logger.info("✅ langchain-community DuckDuckGo 搜索工具可用")
except ImportError:
    HAS_DUCKDUCKGO_TOOL = False
    logger.warning("⚠️ DuckDuckGo 搜索工具未安装，请安装: pip install duckduckgo-search")


def _search_via_ddgs(query: str, max_results: int = 5) -> str:
    try:
        from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            search_results = list(ddgs.text(query, max_results=max_results))

        if not search_results:
            return "未找到相关结果"

        formatted = []
        for i, result in enumerate(search_results, 1):
            title = result.get("title", "无标题")
            url = result.get("href", "")
            content = result.get("body", "")[:300]
            formatted.append(f"{i}. {title}\n   URL: {url}\n   摘要: {content}")

        output = "搜索结果：\n\n" + "\n\n".join(formatted)
        logger.info(f"🔍 DuckDuckGo 搜索完成，返回 {len(search_results)} 条结果")
        return output

    except ImportError:
        raise ValueError("DuckDuckGo 搜索需要安装: pip install duckduckgo-search") from None
    except Exception as e:
        raise RuntimeError(f"DuckDuckGo 搜索失败: {e!s}") from e


def _search_via_langchain(query: str, max_results: int = 5) -> str:
    if not HAS_DUCKDUCKGO_TOOL:
        raise ValueError("DuckDuckGo 搜索工具未安装: pip install duckduckgo-search")

    search = DuckDuckGoSearch(max_results=max_results)
    result = search.invoke(query)

    if isinstance(result, str):
        return result

    if isinstance(result, list):
        formatted = []
        for i, item in enumerate(result, 1):
            if isinstance(item, dict):
                title = item.get("title", "无标题")
                url = item.get("link", item.get("href", ""))
                content = item.get("snippet", item.get("content", ""))[:300]
                formatted.append(f"{i}. {title}\n   URL: {url}\n   摘要: {content}")
            else:
                formatted.append(f"{i}. {item}")
        return "搜索结果：\n\n" + "\n\n".join(formatted)

    return str(result)


class DuckDuckGoSearchInput(BaseModel):
    query: str = Field(description="搜索查询关键词")
    max_results: int = Field(default=5, description="最大返回结果数，默认5条")


class DuckDuckGoSearchTool(BaseTool):
    name: str = "duckduckgo_search"
    metadata: dict = Field(
        default_factory=lambda: {"tier": "extended", "visibility": "switch", "category": "web_search"}
    )
    description: str = (
        "使用 DuckDuckGo 搜索互联网获取信息（无需 API Key）。"
        "适用场景：需要搜索最新信息、新闻、技术更新，且未配置 Tavily API Key 时使用。"
        "不适用：数学计算、代码执行、本地文件操作、需要高精度搜索结果的场景。"
        "参数：query-搜索关键词（必填），max_results-最大返回结果数（1-10，默认5）。"
        "边界：依赖 duckduckgo-search 库，搜索频率受限，结果精度可能不如 Tavily。"
    )
    args_schema: type[BaseModel] = DuckDuckGoSearchInput

    def _run(self, query: str, max_results: int = 5) -> str:
        logger.info(f"🔍 DuckDuckGo 搜索: {query}")

        try:
            return _search_via_ddgs(query, max_results)
        except ImportError:
            logger.warning("⚠️ duckduckgo-search 未安装，尝试使用 langchain-community 工具")
        except Exception as e:
            logger.warning(f"⚠️ DDGS 搜索失败: {e}，尝试 langchain-community 工具")

        try:
            return _search_via_langchain(query, max_results)
        except Exception as e:
            error_msg = f"搜索失败: {e!s}。请安装 duckduckgo-search: pip install duckduckgo-search"
            logger.exception(error_msg)
            return error_msg

    async def _arun(self, query: str, max_results: int = 5) -> str:
        return self._run(query=query, max_results=max_results)


duckduckgo_search = DuckDuckGoSearchTool()


def has_duckduckgo_available() -> bool:
    try:
        from duckduckgo_search import DDGS  # noqa: F401  # 仅用于可用性检测（ImportError 表示未安装）

        return True
    except ImportError:
        return HAS_DUCKDUCKGO_TOOL


def get_duckduckgo_tools():
    if has_duckduckgo_available():
        return [duckduckgo_search]
    logger.warning("⚠️ DuckDuckGo 搜索不可用，请安装: pip install duckduckgo-search")
    return []


DUCKDUCKGO_TOOLS = get_duckduckgo_tools()
