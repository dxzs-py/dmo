"""
请求工具函数

提取公共的请求处理逻辑，遵循 DRY 原则。
"""


def get_client_ip(request):
    """从请求中提取客户端真实 IP 地址。

    安全策略（防 IP 伪造）：
        - ``NUM_PROXIES = 0``（默认）：完全不信任 ``X-Forwarded-For``，直接用 ``REMOTE_ADDR``。
          适用于开发环境（无反向代理）或生产环境未配置可信代理数的场景。
        - ``NUM_PROXIES = N > 0``：仅信任 ``X-Forwarded-For`` 倒数第 N 个 IP。
          反向代理会在 XFF 末尾追加真实客户端 IP，攻击者只能控制 XFF 左侧的伪造项，
          倒数第 N 个仍是真实客户端 IP（与 DRF ``BaseThrottle.get_ident`` 一致）。

    XFF 格式：``client, proxy1, proxy2, ...``（左→右为请求经过的链路顺序）。
    经 N 个可信代理后，XFF 含 N 项，倒数第 N 项即 ``client``。

    Args:
        request: HTTP 请求对象。

    Returns:
        str | None: 客户端真实 IP；request 为 None 时返回 None。
    """
    if request is None:
        return None

    from django.conf import settings as django_settings

    num_proxies = getattr(django_settings, "NUM_PROXIES", 0) or 0

    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    remote_addr = request.META.get("REMOTE_ADDR")

    if num_proxies > 0 and xff:
        addrs = [ip.strip() for ip in xff.split(",") if ip.strip()]
        if addrs:
            # 取倒数第 N 个 IP（min 防止 XFF 项数不足时越界）
            idx = min(-1 * num_proxies, len(addrs))
            return addrs[idx]
        return remote_addr

    # NUM_PROXIES=0：不信任 XFF，直接用 REMOTE_ADDR
    return remote_addr


def get_user_agent(request, max_length=500):
    if request is None:
        return ""
    return request.META.get("HTTP_USER_AGENT", "")[:max_length]
