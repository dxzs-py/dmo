"""通用网络防护工具包。

提供跨模块共享的 SSRF（服务端请求伪造）防护校验，
供 MCP Server URL 校验、web_fetch 抓取等所有服务端出站请求复用。
"""

from Django_xm.common.net.guards import (
    SSRF_EXEMPT_SETTING,
    BlockedAddressError,
    assert_url_not_private,
    is_blocked_ip,
    is_ssrf_exempt,
)

__all__ = [
    "SSRF_EXEMPT_SETTING",
    "BlockedAddressError",
    "assert_url_not_private",
    "is_blocked_ip",
    "is_ssrf_exempt",
]
