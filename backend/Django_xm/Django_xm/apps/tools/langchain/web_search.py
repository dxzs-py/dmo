import logging

from langchain_core.tools import BaseTool
from langchain_tavily import TavilySearch
from pydantic import BaseModel, Field

from Django_xm.apps.tools.base import AsyncToolMixin, SafeConfigMixin
from Django_xm.apps.tools.errors import TOOL_VERSION, StandardToolResult, ToolStatus
from Django_xm.apps.tools.langchain.external_content import wrap_external_content

logger = logging.getLogger(__name__)


def get_tavily_api_key() -> str | None:
    return SafeConfigMixin.get_config("tavily_api_key", env_key="TAVILY_API_KEY")


def get_tavily_max_results() -> int:
    val = SafeConfigMixin.get_config("tavily_max_results", default=5, env_key="TAVILY_MAX_RESULTS")
    return int(val)


def create_tavily_search_tool(
    max_results: int | None = None,
    search_depth: str = "advanced",
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
):
    """创建 Tavily 搜索工具实例

    依赖 langchain-tavily（已写入 requirements.txt），不再兼容 langchain_community 路径。
    """
    tavily_api_key = get_tavily_api_key()
    if not tavily_api_key:
        raise ValueError("Tavily API Key 未设置！请在环境变量或 .env 文件中设置 TAVILY_API_KEY")

    max_results = max_results or get_tavily_max_results()

    logger.info(f"🔍 创建 Tavily 搜索工具 (max_results={max_results}, depth={search_depth})")

    tool_kwargs: dict = {
        "max_results": max_results,
        "api_key": tavily_api_key,
        "search_depth": search_depth,
    }
    if include_domains is not None:
        tool_kwargs["include_domains"] = include_domains
    if exclude_domains is not None:
        tool_kwargs["exclude_domains"] = exclude_domains

    try:
        tool_instance = TavilySearch(**tool_kwargs)
        return tool_instance
    except Exception:
        logger.exception("❌ 创建 Tavily 搜索工具失败")
        raise


def _classify_search_error(e: Exception) -> str:
    """将搜索异常分类为用户可读文案（安全修复 2.4）。

    原始异常文本（可能含 API Key 片段、内部 URL、请求头等敏感信息）
    不得进入模型上下文——仅按错误类别返回固定文案，原始异常由调用方
    记录到日志留痕。

    分类维度：认证/配额、网络连接、服务端不可用、未知（兜底）。
    "无结果"不属异常路径（结果为空时走 PARTIAL 分支返回固定文案）。
    """
    detail = f"{type(e).__name__}: {e}".lower()
    if any(
        kw in detail
        for kw in (
            "401",
            "403",
            "429",
            "unauthorized",
            "forbidden",
            "invalid api key",
            "api key",
            "authentication",
            "quota",
            "rate limit",
            "exceeded",
        )
    ):
        return "搜索失败：搜索服务认证或配额异常，请检查 TAVILY_API_KEY 配置或稍后重试。"
    if any(
        kw in detail
        for kw in (
            "timeout",
            "timed out",
            "connection",
            "network",
            "unreachable",
            "getaddrinfo",
            "ssl",
            "dns",
        )
    ):
        return "搜索失败：网络连接异常，请检查网络后重试。"
    if any(
        kw in detail
        for kw in (
            "500",
            "502",
            "503",
            "504",
            "service unavailable",
            "internal server error",
            "bad gateway",
        )
    ):
        return "搜索失败：搜索服务暂时不可用，请稍后重试。"
    return "搜索失败：搜索服务返回异常，请稍后重试或调整搜索关键词。"


class WebSearchInput(BaseModel):
    query: str = Field(description="搜索查询关键词")


class WebSearchTool(AsyncToolMixin, BaseTool):
    name: str = "web_search"
    version: str = TOOL_VERSION
    metadata: dict = Field(
        default_factory=lambda: {"tier": "extended", "visibility": "switch", "category": "web_search"}
    )
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

            # CT-04：搜索快照属外部内容，进模型上下文前按 B4 语义包裹
            #（包裹在截断之后，信封完整；错误分支为系统生成文案，不包裹）
            if isinstance(results, str):
                return StandardToolResult(
                    content=wrap_external_content("网页搜索 tavily", results),
                    source="tavily",
                ).to_tool_message()

            if not isinstance(results, list):
                return StandardToolResult(
                    content=wrap_external_content("网页搜索 tavily", str(results)),
                    source="tavily",
                ).to_tool_message()

            formatted_results = []
            for i, result in enumerate(results, 1):
                if isinstance(result, str):
                    formatted_results.append(f"{i}. {result}")
                    continue
                if not isinstance(result, dict):
                    formatted_results.append(f"{i}. {result!s}")
                    continue
                title = result.get("title", "无标题")
                url = result.get("url", "")
                content = result.get("content", "")[:300]

                formatted_results.append(f"{i}. {title}\n   URL: {url}\n   摘要: {content}...")

            output = "搜索结果：\n\n" + "\n\n".join(formatted_results)
            logger.info(f"🔍 搜索完成，返回 {len(results)} 条结果")
            return StandardToolResult(
                content=wrap_external_content("网页搜索 tavily", output),
                metadata={"result_count": len(results)},
                source="tavily",
            ).to_tool_message()

        except Exception as e:
            # 安全修复 2.4：异常脱敏——原始异常（可能携带 API Key 片段/内部
            # URL/请求头）只写日志留痕，进入模型上下文的仅分类后的固定文案。
            # 同文件另一处 except（create_tavily_search_tool）为 logger 后原样
            # raise，异常最终汇入本分支统一脱敏，不直接外泄。
            logger.warning("搜索失败(%s): %s", type(e).__name__, e)
            error_msg = _classify_search_error(e)
            return StandardToolResult(
                content=error_msg,
                status=ToolStatus.ERROR,
                source="tavily",
            ).to_tool_message()


web_search = WebSearchTool()


def get_web_search_tools():
    return [web_search]
