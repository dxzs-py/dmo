"""SSRF 内网防护共享守卫（审计项 S-07 / S-08）。

从 apps/tools/serializers.py 抽取的与框架无关的 URL/IP 校验逻辑，
供所有服务端出站请求场景复用（MCP Server URL 校验、web_fetch 抓取等）：

- 覆盖：回环（127.0.0.0/8、::1）、RFC1918（10/8、172.16/12、192.168/16）、
  链路本地（169.254/16，含云元数据 169.254.169.254）、0.0.0.0/8；
  IPv6-mapped IPv4（如 ::ffff:127.0.0.1）先还原为 IPv4 再判断。
- 规则：对 URL 主机名先经 socket.getaddrinfo 解析出全部 IP，逐一判断是否
  落入禁止网段，任一 IP 命中即拒绝；解析失败一律拒绝（防止依赖运行环境
  差异绕过）。IP 字面量主机不走 DNS，直接判定。
- 豁免：本机部署且目标确实运行于本机/内网的合法场景，可在 Django settings
  显式设置 MCP_SSRF_ALLOW_PRIVATE_ADDRESSES = True 豁免（默认关闭）。
"""

import ipaddress
import socket
from urllib.parse import urlparse

from django.conf import settings

# SSRF 内网豁免开关：本机部署且目标确实运行于本机/内网的合法场景，
# 可在 Django settings 显式设置 MCP_SSRF_ALLOW_PRIVATE_ADDRESSES = True 豁免
# 内网地址校验（默认关闭，不写死放行）。
SSRF_EXEMPT_SETTING = "MCP_SSRF_ALLOW_PRIVATE_ADDRESSES"

# 0.0.0.0/8 网段（ipaddress 的 is_unspecified 仅覆盖 0.0.0.0 单地址，需按网段判断）
UNSPECIFIED_V4_NETWORK = ipaddress.ip_network("0.0.0.0/8")


class BlockedAddressError(Exception):
    """URL 指向内网/受限地址（或无法安全解析）时抛出，消息为面向用户的中文描述。"""


def is_ssrf_exempt() -> bool:
    """读取 settings 的 SSRF 豁免开关（默认关闭）。"""
    return bool(getattr(settings, SSRF_EXEMPT_SETTING, False))


def is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """判断 IP 是否落入 SSRF 禁止网段。

    覆盖：回环（127.0.0.0/8、::1）、RFC1918（10/8、172.16/12、192.168/16）、
    链路本地（169.254/16，含云元数据 169.254.169.254）、0.0.0.0/8；
    IPv6-mapped IPv4（如 ::ffff:127.0.0.1）先还原为 IPv4 再判断。
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_loopback or ip.is_link_local or ip.is_private:
        return True
    return ip in UNSPECIFIED_V4_NETWORK


def assert_url_not_private(url: str) -> None:
    """校验 URL 不指向内网/受限地址（SSRF 防护），不合法时抛 BlockedAddressError。

    规则：对 URL 主机名先经 socket.getaddrinfo 解析出全部 IP，逐一判断是否
    落入禁止网段（回环 / RFC1918 / 169.254 链路本地 / 0.0.0.0/8），任一 IP
    命中即拒绝；解析失败一律拒绝（防止依赖运行环境差异绕过）。IP 字面量
    主机不走 DNS，直接判定。本机部署的合法内网目标可通过 settings 显式
    设置 MCP_SSRF_ALLOW_PRIVATE_ADDRESSES = True 豁免（默认关闭）。

    Args:
        url: 待校验的目标 URL

    Raises:
        BlockedAddressError: URL 缺少主机名、无法解析或命中禁止网段
    """
    if is_ssrf_exempt():
        return
    hostname = urlparse(url).hostname
    if not hostname:
        raise BlockedAddressError("URL 缺少主机名，无法校验")
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except OSError:
        # gaierror 为 OSError 子类；DNS 解析失败一律拒绝
        raise BlockedAddressError(f"无法解析主机 '{hostname}'，已拒绝该 URL") from None
    for info in addr_infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if is_blocked_ip(ip):
            raise BlockedAddressError(f"URL 不允许指向内网、回环或链路本地地址（{ip}）")
