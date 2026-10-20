"""web_fetch 内网防护测试（审计项 S-08 + R3 1.8 TOCTOU 钉扎/流式限额）。

覆盖：
- 内网 URL（回环/RFC1918/链路本地，getaddrinfo 解析后判定）→ 请求前拒绝，
  且不发起任何网络请求
- 公网 URL → 正常抓取并转换为纯文本（流式读取断言）
- 公网入口 30x 重定向跳转至内网地址 → 每一跳校验后拒绝，不跟随该跳
- R3 1.8 DNS 钉扎：校验通过的解析结果供实际建连使用（线程级钉扎表命中），
  防止校验后 DNS rebinding
- R3 1.8 钉扎状态清理与线程隔离

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.tools.tests.test_web_fetch_security --noinput
"""

import os
import socket
import sys
import threading
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import SimpleTestCase

# 注意：Django_xm.apps.tools.langchain 包内有同名单例属性 web_fetch（工具实例），
# `from ...langchain import web_fetch` 拿到的是工具而非模块——直连模块路径导入。
# _pinned_getaddrinfo 经模块对象属性访问（from-import 会拷贝 import 时刻的旧值，
# _install_pinned_resolver 重装后模块级变量已指向新包装）；包属性遮蔽子模块名
# （import as 亦被遮蔽），必须经 sys.modules 取子模块对象。
from Django_xm.apps.tools.langchain.web_fetch import (
    _clear_pinned_resolution,
    _fetch_url,
    _install_pinned_resolver,
    _pinned_resolver_state,
)

_web_fetch_module = sys.modules["Django_xm.apps.tools.langchain.web_fetch"]

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


class _FakeResponse:
    """最小 requests.Response 替身：流式读取口径（iter_content 分块产出）+
    上下文管理器协议（with requests.get(...) as resp）。"""

    def __init__(self, status_code: int = 200, headers: dict | None = None, text: str = "", chunk_size: int = 32):
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}
        self.text = text
        self.encoding = "utf-8"
        encoded = text.encode("utf-8")
        self._chunks = [encoded[i : i + chunk_size] for i in range(0, len(encoded), chunk_size)] or [b""]

    def iter_content(self, chunk_size=None):
        yield from self._chunks

    def raise_for_status(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


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
        response = _FakeResponse(text="<html><body><p>公开网页内容</p></body></html>")
        with patch(GUARDS_GETADDRINFO, side_effect=getaddrinfo), patch(
            WEB_FETCH_GET, return_value=response
        ) as mock_get:
            result = _fetch_url("http://example.com/page")
        assert "公开网页内容" in result
        assert not result.startswith("错误:")
        # 禁用 requests 自动重定向（allow_redirects=False），由 _fetch_url 逐跳校验
        assert mock_get.call_args.kwargs.get("allow_redirects") is False
        # R3 1.8：流式读取（stream=True）
        assert mock_get.call_args.kwargs.get("stream") is True

    def test_redirect_to_private_host_rejected(self):
        """公网入口 30x 重定向至内网地址 → 该跳校验后拒绝，不发起内网请求。"""
        getaddrinfo = _make_getaddrinfo(
            {"example.com": "93.184.216.34", "169.254.169.254": "169.254.169.254"}
        )
        redirect_response = _FakeResponse(
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


class TestWebFetchDnsPinning(SimpleTestCase):
    """R3 1.8：DNS 钉扎——校验通过的解析结果供实际建连使用（TOCTOU 修复）。"""

    def setUp(self):
        _clear_pinned_resolution()

    def tearDown(self):
        _clear_pinned_resolution()

    def test_pinned_resolution_used_at_connect_time(self):
        """建连时刻（requests.get 调用内）解析命中钉扎表：返回预校验公网 IP，
        后续 rebinding 解析（内网 IP）不影响建连解析。"""
        calls = {"n": 0}

        def rebinding_getaddrinfo(host, *args, **kwargs):
            calls["n"] += 1
            ip = "93.184.216.34" if calls["n"] == 1 else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

        captured = {}

        def fake_get(url, **kwargs):
            # requests.get 执行时刻：模拟建连解析（urllib3 → socket.getaddrinfo）
            captured["at_connect"] = socket.getaddrinfo("rebind.example.com", 8080)
            return _FakeResponse(text="ok")

        with patch(GUARDS_GETADDRINFO, side_effect=rebinding_getaddrinfo), patch(
            WEB_FETCH_GET, side_effect=fake_get
        ):
            _fetch_url("http://rebind.example.com/page")

        entries = captured["at_connect"]
        assert entries, "建连时刻应命中钉扎表"
        assert entries[0][4][0] == "93.184.216.34", (
            f"建连解析应使用预校验公网 IP（防 rebinding），实际: {entries[0][4]}"
        )
        assert entries[0][4][1] == 8080, "钉扎解析应按建连端口改写"

    def test_pinned_state_cleared_after_request(self):
        """请求结束后本线程钉扎表必须清理（to_thread 工作线程复用防残留）。"""
        getaddrinfo = _make_getaddrinfo({"example.com": "93.184.216.34"})
        response = _FakeResponse(text="ok")
        with patch(GUARDS_GETADDRINFO, side_effect=getaddrinfo), patch(
            WEB_FETCH_GET, return_value=response
        ):
            _fetch_url("http://example.com/page")
        assert not (_pinned_resolver_state.map or {})

    def test_pin_is_thread_local(self):
        """钉扎表按线程隔离：A 线程的钉扎不泄漏到 B 线程。"""
        _pinned_resolver_state.map = {"example.com": [("x",)]}
        observed = {}

        def probe():
            observed["other_thread"] = dict(getattr(_pinned_resolver_state, "map", None) or {})

        t = threading.Thread(target=probe)
        t.start()
        t.join()
        assert "example.com" not in observed["other_thread"]

    def test_resolver_reinstalls_after_external_override(self):
        """幂等 = 在位判定（产品语义回归）：包装被外部（如 mock.patch）覆盖后，
        再次安装必须重装——原实现进程级一次性标记（installed 永真）导致包装
        被覆盖后永不恢复，建连解析脱离钉扎表（TOCTOU 防护整体失效）。"""
        original_getaddrinfo = socket.getaddrinfo
        try:
            # 首次安装：包装在位
            _install_pinned_resolver()
            assert _web_fetch_module._pinned_getaddrinfo is not None
            assert socket.getaddrinfo is _web_fetch_module._pinned_getaddrinfo

            # 幂等：在位时重复安装不替换（同一对象）
            _install_pinned_resolver()
            assert socket.getaddrinfo is _web_fetch_module._pinned_getaddrinfo

            # 模拟外部覆盖（mock.patch / 其他库临时替换）
            def _external_override(host, *args, **kwargs):
                raise AssertionError("外部覆盖实现不应被钉扎路径调用")

            socket.getaddrinfo = _external_override

            # 再次安装：不在位 → 重装（原实现在此处因 installed 永真而跳过）
            _install_pinned_resolver()
            assert socket.getaddrinfo is _web_fetch_module._pinned_getaddrinfo, (
                "包装被外部覆盖后必须重装（幂等=在位判定）"
            )
        finally:
            # 还原进入时的 socket.getaddrinfo（不污染其他测试）
            socket.getaddrinfo = original_getaddrinfo

    def test_pinned_wrapper_passthrough_miss(self):
        """包装未命中钉扎表时透传底层解析（_socket.getaddrinfo），不抛异常。"""
        original_getaddrinfo = socket.getaddrinfo
        try:
            _install_pinned_resolver()
            _clear_pinned_resolution()
            # 未注册主机：透传底层实现（IP 字面量不触 DNS，确定性返回）
            entries = socket.getaddrinfo("127.0.0.1", 0)
            assert entries, "未命中钉扎表应透传底层解析"
            assert any(e[4][0] == "127.0.0.1" for e in entries)
        finally:
            socket.getaddrinfo = original_getaddrinfo
            _clear_pinned_resolution()
