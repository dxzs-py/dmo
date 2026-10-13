import asyncio
import ipaddress
import logging
import re
import socket
import threading
from urllib.parse import urljoin, urlparse

import requests
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from Django_xm.apps.tools.errors import TOOL_VERSION, StandardToolResult, ToolStatus
from Django_xm.apps.tools.langchain.external_content import wrap_external_content
from Django_xm.common.net.guards import (
    BlockedAddressError,
    is_blocked_ip,
    is_ssrf_exempt,
)

logger = logging.getLogger(__name__)

MAX_CONTENT_LENGTH = 50000
REQUEST_TIMEOUT = 30
# 重定向逐跳跟随上限：每一跳目标均需通过内网防护校验（S-08），
# 防止公网入口 30x 跳转至内网/元数据地址绕过校验
MAX_REDIRECTS = 5
# 需要跟随的重定向状态码（与 requests 默认重定向语义一致，不含 300/304）
REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
# R3 1.8：响应体流式读取的原始字节硬上限——原实现 response.text 全量入内存
# 再截断，超大响应（如数百分片大文件）会占满进程内存；流式累计到此上限即停
MAX_RAW_BYTES = 512 * 1024
READ_CHUNK_SIZE = 64 * 1024


# ── DNS 钉扎（R3 1.8：SSRF 校验与实际建连间的 DNS rebinding TOCTOU 修复）──
# 原实现：assert_url_not_private 先解析校验 → requests.get 再自行解析建连，
# 两次解析之间 DNS 可被 rebinding（首次解析公网 IP 过校验，建连时解析为内网 IP）。
# 修复：本模块先解析一次 → 复用 guards.is_blocked_ip 逐一校验（对齐 guards
# 语义）→ 将校验通过的解析结果钉扎到线程级解析表 → requests/urllib3 建连时
# 经包装后的 socket.getaddrinfo 命中钉扎表，直接使用已校验的解析结果。
# 线程级（threading.local）而非进程级：web_fetch 经 asyncio.to_thread 在工作
# 线程同步执行，钉扎表生命周期与本次请求同线程同请求，finally 清理；
# 并发请求各自线程独立，互不串扰。
_pinned_resolver_state = threading.local()
_pinned_resolver_installed = False
_pinned_install_lock = threading.Lock()
_original_getaddrinfo = None


def _install_pinned_resolver() -> None:
    """安装一次性 socket.getaddrinfo 包装（幂等）：命中线程级钉扎表时返回
    预校验的解析结果（端口按调用方请求改写），未命中透传原始解析。"""
    global _pinned_resolver_installed, _original_getaddrinfo
    if _pinned_resolver_installed:
        return
    with _pinned_install_lock:
        if _pinned_resolver_installed:
            return
        _original_getaddrinfo = socket.getaddrinfo

        def _pinned_getaddrinfo(host, port, *args, **kwargs):
            pinned_map = getattr(_pinned_resolver_state, "map", None) or {}
            entries = pinned_map.get(host)
            if not entries:
                return _original_getaddrinfo(host, port, *args, **kwargs)
            if port is None:
                return list(entries)
            # 请求方（socket.create_connection）把端口编进 sockaddr；
            # 钉扎表预解析时端口为占位 0，按调用端口改写（IPv6 含 flowinfo/scope_id 后缀）
            rewritten = []
            for family, type_, proto, canonname, sa in entries:
                if isinstance(sa, tuple) and len(sa) >= 2:
                    sa = (sa[0], port) + tuple(sa[2:])
                rewritten.append((family, type_, proto, canonname, sa))
            return rewritten

        socket.getaddrinfo = _pinned_getaddrinfo
        _pinned_resolver_installed = True


def _pin_url_resolution(current_url: str) -> None:
    """解析 URL 主机 → guards 口径校验每个解析 IP → 钉扎到线程级解析表。

    - 豁免开关（MCP_SSRF_ALLOW_PRIVATE_ADDRESSES）开启时跳过（与守卫一致）；
    - IP 字面量主机经 getaddrinfo 直接返回自身，同样参与网段判定；
    - 校验失败抛 BlockedAddressError（由调用方转为结构化错误）。
    """
    if is_ssrf_exempt():
        return
    hostname = urlparse(current_url).hostname
    if not hostname:
        raise BlockedAddressError("URL 缺少主机名，无法校验")
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except OSError:
        # gaierror 为 OSError 子类；DNS 解析失败一律拒绝（对齐 guards.py）
        raise BlockedAddressError(f"无法解析主机 '{hostname}'，已拒绝该 URL") from None
    for info in addr_infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if is_blocked_ip(ip):
            raise BlockedAddressError(f"URL 不允许指向内网、回环或链路本地地址（{ip}）")
    _install_pinned_resolver()
    pinned_map = getattr(_pinned_resolver_state, "map", None)
    if pinned_map is None:
        pinned_map = {}
        _pinned_resolver_state.map = pinned_map
    pinned_map[hostname] = list(addr_infos)


def _clear_pinned_resolution() -> None:
    """清理本线程钉扎表（to_thread 工作线程会被复用，必须逐次清理防残留）。"""
    _pinned_resolver_state.map = {}


def _html_to_text(html: str) -> str:
    html = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", html, flags=re.IGNORECASE)
    html = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", html, flags=re.IGNORECASE)
    html = re.sub(r"<nav[^>]*>[\s\S]*?</nav>", "", html, flags=re.IGNORECASE)
    html = re.sub(r"<footer[^>]*>[\s\S]*?</footer>", "", html, flags=re.IGNORECASE)
    html = re.sub(r"<header[^>]*>[\s\S]*?</header>", "", html, flags=re.IGNORECASE)

    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<p[^>]*>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</p>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<h[1-6][^>]*>", "\n## ", html, flags=re.IGNORECASE)
    html = re.sub(r"</h[1-6]>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<li[^>]*>", "\n- ", html, flags=re.IGNORECASE)
    html = re.sub(r"<div[^>]*>", "\n", html, flags=re.IGNORECASE)

    html = re.sub(r"<[^>]+>", "", html)

    html = re.sub(r"&nbsp;", " ", html)
    html = re.sub(r"&amp;", "&", html)
    html = re.sub(r"&lt;", "<", html)
    html = re.sub(r"&gt;", ">", html)
    html = re.sub(r"&quot;", '"', html)
    html = re.sub(r"&#\d+;", "", html)

    lines = html.split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            cleaned_lines.append(stripped)

    text = "\n".join(cleaned_lines)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def _read_body_streamed(response) -> tuple[str, bool]:
    """流式读取响应体并限额（R3 1.8）。

    按 READ_CHUNK_SIZE 分块累计到 MAX_RAW_BYTES 即停止读取，不将超大响应
    全量载入内存。解码优先响应头 charset，缺省 utf-8（非法序列 replace）。

    Returns:
        (body 文本, 是否因超限被截断)
    """
    raw = bytearray()
    truncated = False
    for chunk in response.iter_content(chunk_size=READ_CHUNK_SIZE):
        raw.extend(chunk)
        if len(raw) >= MAX_RAW_BYTES:
            truncated = True
            break
    encoding = getattr(response, "encoding", None) or "utf-8"
    body = bytes(raw).decode(encoding, errors="replace")
    return body, truncated


def _fetch_url(url: str, prompt: str | None = None) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # S-08 内网防护：禁用 requests 自动重定向（allow_redirects=False），
    # 改为手动逐跳跟随，每一跳目标均先经 DNS 预解析 + 守卫口径校验后再发起
    # 请求（R3 1.8：校验通过的解析结果钉扎到线程级解析表供实际建连使用，
    # 消除"校验时解析"与"建连时解析"之间的 DNS rebinding TOCTOU 窗口）
    pinned_map = getattr(_pinned_resolver_state, "map", None)
    if pinned_map is None:
        _pinned_resolver_state.map = {}
    try:
        current_url = url
        for _ in range(MAX_REDIRECTS + 1):
            try:
                _pin_url_resolution(current_url)
            except BlockedAddressError as e:
                # 结构化中文错误：由 _run 包装为 ToolStatus.ERROR 结果
                return f"错误: {e}"

            # R3 1.8：stream=True 流式读取（原始字节限额见 _read_body_streamed）
            with requests.get(
                current_url,
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
                stream=True,
            ) as response:
                if response.status_code in REDIRECT_STATUS_CODES:
                    location = response.headers.get("Location", "")
                    if not location:
                        return "错误: 目标服务器返回重定向但缺少跳转地址"
                    # 相对路径 Location 按当前 URL 解析为绝对地址后再校验
                    current_url = urljoin(current_url, location)
                    continue

                response.raise_for_status()

                content_type = response.headers.get("Content-Type", "")
                body, truncated = _read_body_streamed(response)

                if "application/json" in content_type:
                    try:
                        import json

                        json_data = json.loads(body)
                        result_text = json.dumps(json_data, ensure_ascii=False, indent=2)[:MAX_CONTENT_LENGTH]
                        if truncated:
                            result_text += "\n\n...(响应体超限已截断，JSON 可能不完整)"
                        return result_text
                    except Exception:
                        # JSON 解析失败时回退到 HTML 解析
                        logger.debug("JSON 解析失败，回退到 HTML 解析")

                text = _html_to_text(body)

                if len(text) > MAX_CONTENT_LENGTH:
                    text = text[:MAX_CONTENT_LENGTH] + "\n\n...(内容过长已截断)"
                if truncated:
                    text += f"\n\n...(响应体超过 {MAX_RAW_BYTES // 1024}KB 读取上限，已停止下载)"

                return text

        return f"错误: 重定向次数超过上限（{MAX_REDIRECTS}次）"

    except requests.Timeout:
        return f"错误: 请求超时 ({REQUEST_TIMEOUT}秒)"
    except requests.ConnectionError:
        return "错误: 无法连接到目标服务器"
    except requests.HTTPError as e:
        return f"错误: HTTP {e.response.status_code}"
    except Exception as e:
        return f"错误: {e!s}"
    finally:
        _clear_pinned_resolution()


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
            # B4 注入隔离：先截断后包裹，保证信封完整（长度软上限偏差见模块说明）
            result = wrap_external_content(f"网页 {url}", result)
            return StandardToolResult(
                content=result,
                source="web_fetch",
                metadata={"url": url, "prompt": prompt},
            ).to_tool_message()

        result = f"网页内容 ({url}):\n\n{content}"
        if len(result) > MAX_CONTENT_LENGTH:
            result = result[:MAX_CONTENT_LENGTH]
        result = wrap_external_content(f"网页 {url}", result)
        return StandardToolResult(
            content=result,
            source="web_fetch",
            metadata={"url": url},
        ).to_tool_message()

    async def _arun(self, url: str, prompt: str = "") -> str:
        # R3 1.8：同步 _run（requests 网络 I/O）经 to_thread 移入线程池，
        # 不再直接在事件循环内阻塞
        return await asyncio.to_thread(self._run, url=url, prompt=prompt)


web_fetch = WebFetchTool()


def get_web_fetch_tools():
    return [web_fetch]
