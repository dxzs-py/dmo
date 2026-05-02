import re
import logging
from typing import Optional
import requests
from langchain_core.tools import tool

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


def _fetch_url(url: str, prompt: Optional[str] = None) -> str:
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
        return f"错误: {str(e)}"


@tool
def web_fetch(url: str, prompt: str = "") -> str:
    """抓取网页内容并返回文本。支持将URL的HTML内容转换为纯文本。

    Args:
        url: 要抓取的网页URL
        prompt: 可选的提示词，用于指定需要提取的信息类型
    """
    logger.info(f"WebFetch: {url}")

    content = _fetch_url(url, prompt if prompt else None)

    if content.startswith("错误:"):
        return content

    if prompt:
        result = f"网页内容 ({url}):\n\n{content}"
        if len(result) > MAX_CONTENT_LENGTH:
            result = result[:MAX_CONTENT_LENGTH]
        return result

    return f"网页内容 ({url}):\n\n{content}"


def get_web_fetch_tools():
    return [web_fetch]
