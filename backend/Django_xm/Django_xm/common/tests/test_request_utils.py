"""get_client_ip 单元测试（Task 11.5）。

覆盖 spec `fix-backend-audit-findings` SubTask 11.1：配合 NUM_PROXIES 防 X-Forwarded-For 伪造。

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python manage.py test Django_xm.common.tests.test_request_utils --verbosity=2
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock

# Django 环境初始化（兼容 unittest 直接运行）
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from django.test import override_settings

from Django_xm.common.request_utils import get_client_ip


def _make_request(xff=None, remote_addr="203.0.113.1"):
    """构造带 META 的请求对象。"""
    request = MagicMock()
    meta = {"REMOTE_ADDR": remote_addr}
    if xff is not None:
        meta["HTTP_X_FORWARDED_FOR"] = xff
    request.META = meta
    return request


class GetClientIpNumProxiesZeroTest(unittest.TestCase):
    """NUM_PROXIES=0（默认）：不信任 X-Forwarded-For，直接用 REMOTE_ADDR。"""

    @override_settings(NUM_PROXIES=0)
    def test_no_xff_returns_remote_addr(self):
        """无 XFF 头时返回 REMOTE_ADDR。"""
        request = _make_request(xff=None, remote_addr="203.0.113.1")
        self.assertEqual(get_client_ip(request), "203.0.113.1")

    @override_settings(NUM_PROXIES=0)
    def test_xff_ignored_when_num_proxies_zero(self):
        """NUM_PROXIES=0 时 XFF 被完全忽略，返回 REMOTE_ADDR（防伪造）。"""
        request = _make_request(xff="1.2.3.4", remote_addr="203.0.113.1")
        self.assertEqual(get_client_ip(request), "203.0.113.1")

    @override_settings(NUM_PROXIES=0)
    def test_spoofed_xff_ignored(self):
        """攻击者伪造 XFF 无法绕过限流（NUM_PROXIES=0 时 XFF 完全不信任）。"""
        request = _make_request(
            xff="10.0.0.1, 10.0.0.2, 10.0.0.3",
            remote_addr="203.0.113.99",
        )
        self.assertEqual(get_client_ip(request), "203.0.113.99")


class GetClientIpNumProxiesOneTest(unittest.TestCase):
    """NUM_PROXIES=1（单层反向代理）：取 XFF 倒数第 1 个 IP。"""

    @override_settings(NUM_PROXIES=1)
    def test_single_proxy_takes_last_xff(self):
        """单层代理：XFF 只有客户端 IP，取倒数第 1 个（即客户端 IP）。"""
        request = _make_request(xff="198.51.100.7", remote_addr="10.0.0.1")
        self.assertEqual(get_client_ip(request), "198.51.100.7")

    @override_settings(NUM_PROXIES=1)
    def test_spoofed_xff_with_real_proxy(self):
        """攻击者伪造 XFF，但反向代理在末尾追加真实客户端 IP。

        场景：攻击者发送 XFF="1.2.3.4"，
        代理追加真实客户端后 XFF="1.2.3.4, real_client_ip"，
        NUM_PROXIES=1 取倒数第 1 个 → real_client_ip（攻击者无法绕过）。
        """
        request = _make_request(
            xff="1.2.3.4, 198.51.100.7",
            remote_addr="10.0.0.1",
        )
        self.assertEqual(get_client_ip(request), "198.51.100.7")

    @override_settings(NUM_PROXIES=1)
    def test_xff_with_spaces_parsed_correctly(self):
        """XFF 各项含空格时正确 strip。"""
        request = _make_request(
            xff="  1.2.3.4 ,  198.51.100.7  ",
            remote_addr="10.0.0.1",
        )
        self.assertEqual(get_client_ip(request), "198.51.100.7")

    @override_settings(NUM_PROXIES=1)
    def test_empty_xff_falls_back_to_remote_addr(self):
        """XFF 为空字符串时回退到 REMOTE_ADDR。"""
        request = _make_request(xff="", remote_addr="203.0.113.1")
        self.assertEqual(get_client_ip(request), "203.0.113.1")

    @override_settings(NUM_PROXIES=1)
    def test_xff_with_only_empty_entries_falls_back(self):
        """XFF 仅含逗号/空格时回退到 REMOTE_ADDR。"""
        request = _make_request(
            xff=" , , ",
            remote_addr="203.0.113.1",
        )
        self.assertEqual(get_client_ip(request), "203.0.113.1")


class GetClientIpNumProxiesTwoTest(unittest.TestCase):
    """NUM_PROXIES=2（双层反向代理，如 CDN + nginx）：取 XFF 倒数第 2 个 IP。"""

    @override_settings(NUM_PROXIES=2)
    def test_double_proxy_takes_second_to_last(self):
        """双层代理：XFF="client, proxy1"，取倒数第 2 个 → client。"""
        request = _make_request(
            xff="198.51.100.7, 10.0.0.1",
            remote_addr="10.0.0.2",
        )
        self.assertEqual(get_client_ip(request), "198.51.100.7")

    @override_settings(NUM_PROXIES=2)
    def test_spoofed_xff_with_two_proxies(self):
        """攻击者伪造 XFF，双层代理后真实客户端在倒数第 2 个位置。"""
        request = _make_request(
            xff="1.2.3.4, 198.51.100.7, 10.0.0.1",
            remote_addr="10.0.0.2",
        )
        # 倒数第 2 个是真实客户端
        self.assertEqual(get_client_ip(request), "198.51.100.7")


class GetClientIpEdgeCaseTest(unittest.TestCase):
    """边界场景。"""

    def test_none_request_returns_none(self):
        """request 为 None 时返回 None。"""
        self.assertIsNone(get_client_ip(None))

    @override_settings(NUM_PROXIES=1)
    def test_xff_fewer_than_num_proxies_does_not_crash(self):
        """XFF 项数少于 NUM_PROXIES 时不越界（min 防护）。"""
        # NUM_PROXIES=1 但 XFF 只有 1 项（实际无代理直连场景）
        request = _make_request(xff="198.51.100.7", remote_addr="10.0.0.1")
        self.assertEqual(get_client_ip(request), "198.51.100.7")


if __name__ == "__main__":
    unittest.main()
