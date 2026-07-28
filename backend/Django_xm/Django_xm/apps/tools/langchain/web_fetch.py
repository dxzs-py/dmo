import logging
import re

import requests
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from Django_xm.apps.tools.errors import TOOL_VERSION, StandardToolResult, ToolStatus

logger = logging.getLogger(__name__)

MAX_CONTENT_LENGTH = 50000
REQUEST_TIMEOUT = 30
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _html_to_text(html: str) -> str:
    html = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', html, flags=re.IGNORECASE)
    html = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', html, flags=re.IGNORECASE)
    html = re.sub(r'<nav[^>]*>[\s\S]*?</nav>', '', html, flags=re.IGNORECASE)
    html = re.sub(r'<footer[^>]*>[\s\S]*?</footer>', '', html, flags=re.IGNORECASE)
    html = re.sub(r'<header[^>]*>[\s\S]*?</header>', '', html, flags=re.IGNORECASE)

    html = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'<p[^>]*>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'</p>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'<h[1-6][^>]*>', '\n## ', html, flags=re.IGNORECASE)
    html = re.sub(r'</h[1-6]>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'<li[^>]*>', '\n- ', html, flags=re.IGNORECASE)
    html = re.sub(r'<div[^>]*>', '\n', html, flags=re.IGNORECASE)

    html = re.sub(r'<[^>]+>', '', html)

    html = re.sub(r'&nbsp;', ' ', html)
    html = re.sub(r'&amp;', '&', html)
    html = re.sub(r'&lt;', '<', html)
    html = re.sub(r'&gt;', '>', html)
    html = re.sub(r'&quot;', '"', html)
    html = re.sub(r'&#\d+;', '', html)

    lines = html.split('\n')
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            cleaned_lines.append(stripped)

    text = '\n'.join(cleaned_lines)
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def _fetch_url(url: str, prompt: str | None = None) -> str:
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        response.raise_for_status()

        content_type = response.headers.get('Content-Type', '')
        if 'application/json' in content_type:
            try:
                import json
                json_data = response.json()
                return json.dumps(json_data, ensure_ascii=False, indent=2)[:MAX_CONTENT_LENGTH]
            except Exception:
                pass

        html = response.text
        if len(html) > 500000:
            html = html[:500000]

        text = _html_to_text(html)

        if len(text) > MAX_CONTENT_LENGTH:
            text = text[:MAX_CONTENT_LENGTH] + "\n\n...(内容过长已截断)"

        return text

    except requests.Timeout:
        return f"错误: 请求超时 ({REQUEST_TIMEOUT}秒)"
    except requests.ConnectionError:
        return "错误: 无法连接到目标服务器"
    except requests.HTTPError as e:
        return f"错误: HTTP {e.response.status_code}"
    except Exception as e:
        return f"错误: {e!s}"


class WebFetchInput(BaseModel):
    url: str = Field(description="要抓取的网页URL")
    prompt: str = Field(default="", description="可选的提示词，用于指定需要提取的信息类型")


class WebFetchTool(BaseTool):
    name: str = "web_fetch"
    version: str = TOOL_VERSION
    metadata: dict = Field(default_factory=lambda: {"tier": "standard", "visibility": "core", "category": "web_fetch"})
    description: str = (
        "抓取指定 URL 的网页内容，将 HTML 转换为纯文本返回，支持 JSON API 响应。"
        "适用场景：需要获取指定网页的详细内容、提取网页信息、读取 API 返回的 JSON 数据。"
        "不适用：搜索引擎式关键词搜索（应使用 web_search）、数学计算、本地文件操作。"
        "参数：url-要抓取的网页 URL（必填，需包含 http:// 或 https://），"
        "prompt-可选提示词（指定需要提取的信息类型，默认为空提取全文）。"
        "边界：请求超时30秒，内容超过50000字符自动截断，部分网站可能拒绝抓取。"
    )
    args_schema: type[BaseModel] = WebFetchInput

    def _run(self, url: str, prompt: str = "") -> str:
        logger.info(f"WebFetch: {url}")

        content = _fetch_url(url, prompt if prompt else None)

        if content.startswith("错误:"):
            return StandardToolResult(
                content=content[3:].strip(),
                status=ToolStatus.ERROR,
                source="web_fetch",
                metadata={"url": url},
            ).to_tool_message()

        if prompt:
            result = f"网页内容 ({url}):\n\n{content}"
            if len(result) > MAX_CONTENT_LENGTH:
                result = result[:MAX_CONTENT_LENGTH]
            return StandardToolResult(
                content=result,
                source="web_fetch",
                metadata={"url": url, "prompt": prompt},
            ).to_tool_message()

        return StandardToolResult(
            content=f"网页内容 ({url}):\n\n{content}",
            source="web_fetch",
            metadata={"url": url},
        ).to_tool_message()

    async def _arun(self, url: str, prompt: str = "") -> str:
        return self._run(url=url, prompt=prompt)


web_fetch = WebFetchTool()


def get_web_fetch_tools():
    return [web_fetch]
