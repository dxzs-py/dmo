"""web_fetch 内网防护测试（审计项 S-08）。

覆盖：
- 内网 URL（回环/RFC1918/链路本地，getaddrinfo 解析后判定）→ 请求前拒绝，
  且不发起任何网络请求
- 公网 URL → 正常抓取并转换为纯文本
- 公网入口 30x 重定向跳转至内网地址 → 每一跳校验后拒绝，不跟随该跳

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.tools.tests.test_web_fetch_security --noinput
"""

import os
from types import SimpleNamespace

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

import socket
from unittest.mock import patch

from django.test import SimpleTestCase

from Django_xm.apps.tools.langchain.web_fetch import _fetch_url

GUARDS_GETADDRINFO = "Django_xm.common.net.guards.socket.getaddrinfo"
WEB_FETCH_GET = "Django_xm.apps.tools.langchain.web_fetch.requests.get"


def _make_getaddrinfo(host_to_ip: dict[str, str]):
    """构造 mock getaddrinfo：按主机名返回固定 IP；未知主机抛 gaierror。"""

    def fake_getaddrinfo(host, *args, **kwargs):
        ip = host_to_ip.get(host)
        if ip is None:
            raise socket.gaierror(f"mock: 未注册主机 {host}")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    return fake_getaddrinfo


def _make_response(status_code: int = 200, headers: dict | None = None, text: str = ""):
    """构造最小 requests.Response 替身（仅 _fetch_url 用到的属性）。"""
    resp = SimpleNamespace(
        status_code=status_code,
        headers=headers or {"Content-Type": "text/html; charset=utf-8"},
        text=text,
    )
    resp.raise_for_status = lambda: None
    return resp


class TestWebFetchSsrfGuard(SimpleTestCase):
    """web_fetch 内网防护（S-08）：请求前校验 + 重定向逐跳校验。"""

    def test_private_url_rejected_before_request(self):
        """回环 URL → 请求前拒绝，且不发起任何网络请求。"""
        getaddrinfo = _make_getaddrinfo({"127.0.0.1.example.com": "127.0.0.1"})
        with patch(GUARDS_GETADDRINFO, side_effect=getaddrinfo), patch(WEB_FETCH_GET) as mock_get:
            result = _fetch_url("http://127.0.0.1.example.com/page")
        assert result.startswith("错误:")
        assert "内网" in result
        mock_get.assert_not_called()

    def test_link_local_url_rejected_before_request(self):
        """链路本地 URL（云元数据 169.254.169.254）→ 请求前拒绝。"""
        getaddrinfo = _make_getaddrinfo({"metadata.example.com": "169.254.169.254"})
        with patch(GUARDS_GETADDRINFO, side_effect=getaddrinfo), patch(WEB_FETCH_GET) as mock_get:
            result = _fetch_url("http://metadata.example.com/latest/meta-data/")
        assert result.startswith("错误:")
        assert "内网" in result
        mock_get.assert_not_called()

    def test_public_url_passes_and_returns_text(self):
        """公网 URL → 正常抓取，HTML 转换为纯文本。"""
        getaddrinfo = _make_getaddrinfo({"example.com": "93.184.216.34"})
        response = _make_response(text="<html><body><p>公开网页内容</p></body></html>")
        with patch(GUARDS_GETADDRINFO, side_effect=getaddrinfo), patch(
            WEB_FETCH_GET, return_value=response
        ) as mock_get:
            result = _fetch_url("http://example.com/page")
        assert "公开网页内容" in result
        assert not result.startswith("错误:")
        # 禁用 requests 自动重定向（allow_redirects=False），由 _fetch_url 逐跳校验
        assert mock_get.call_args.kwargs.get("allow_redirects") is False

    def test_redirect_to_private_host_rejected(self):
        """公网入口 30x 重定向至内网地址 → 该跳校验后拒绝，不发起内网请求。"""
        getaddrinfo = _make_getaddrinfo(
            {"example.com": "93.184.216.34", "169.254.169.254": "169.254.169.254"}
        )
        redirect_response = _make_response(
            status_code=302, headers={"Location": "http://169.254.169.254/latest/meta-data/"}
        )
        with patch(GUARDS_GETADDRINFO, side_effect=getaddrinfo), patch(
            WEB_FETCH_GET, return_value=redirect_response
        ) as mock_get:
            result = _fetch_url("http://example.com/redirect")
        assert result.startswith("错误:")
        assert "内网" in result
        # 仅第一跳（公网入口）发起了请求，内网跳未发起
        assert mock_get.call_count == 1
