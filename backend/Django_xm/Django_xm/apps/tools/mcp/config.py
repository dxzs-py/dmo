"""MCP Server 系统级配置。

从 ``settings/dev.py`` 迁移而来（Task 27.3）。

归属说明：
    MCP Server 配置是 ``apps.tools.mcp`` 模块的职责，由 ``discovery.py``
    直接消费。原放在 ``settings/dev.py`` 违反"配置就近原则"——settings 应
    只承载 Django 框架级配置，业务模块专属配置应归属业务模块。

环境差异策略：
    - 开发环境：内置 2 个默认 MCP Server（sequential-thinking + context7）
      由 ``_build_default_servers()`` 动态构建，解析本机 npx/node 路径
    - 生产环境：默认空列表，应通过 ``MCP_REGISTRY_URL`` 环境变量配置注册中心，
      由 ``MCPServerDiscovery.discover_from_registry()`` 动态发现；
      或通过 ``MCP_SYSTEM_SERVERS_JSON`` 环境变量（JSON 字符串）显式注入

环境变量：
    - ``NPM_NPX_PATH`` / ``NPM_NPX_ALT_PATH``：npx 可执行文件路径（开发环境）
    - ``NODE_PATH``：node 安装目录（开发环境，用于 stdio MCP Server 的 PATH 注入）
    - ``MCP_SYSTEM_SERVERS_JSON``：系统级 MCP Server JSON 配置（生产环境，覆盖默认）
"""

from __future__ import annotations

import json
import os
from typing import Any

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)


# ============== 开发环境路径解析 ==============


def _resolve_npx_path() -> str:
    """解析 npx 可执行文件路径（开发环境专用）。

    优先级：
        1. ``NPM_NPX_PATH`` 环境变量（存在则使用）
        2. ``NPM_NPX_ALT_PATH`` 环境变量（备选路径，存在则使用）
        3. ``'npx'`` 字面量（依赖系统 PATH 解析）
    """
    npx_path = os.environ.get("NPM_NPX_PATH")
    if not npx_path or not os.path.exists(npx_path):
        npx_alt = os.environ.get("NPM_NPX_ALT_PATH")
        npx_path = npx_alt if npx_alt and os.path.exists(npx_alt) else "npx"
    return npx_path


def _resolve_node_dir() -> str | None:
    """解析 node 安装目录（开发环境专用，用于 stdio MCP Server 的 PATH 注入）。

    Returns:
        node 安装目录路径，或 None（未配置或路径不存在）。
    """
    node_dir = os.environ.get("NODE_PATH")
    if node_dir and not os.path.exists(node_dir):
        return None
    return node_dir


def _build_default_servers() -> list[dict[str, Any]]:
    """构建开发环境默认 MCP Server 列表。

    包含：
        - sequential-thinking：结构化渐进式思维工具（stdio 传输，需 npx）
        - context7：实时库文档查询（http 传输）
    """
    npx_path = _resolve_npx_path()
    node_dir = _resolve_node_dir()
    env_path = os.environ.get("PATH", "")
    path_env = f"{node_dir};{env_path}" if node_dir else None

    return [
        {
            "name": "sequential-thinking",
            "transport": "stdio",
            "command": npx_path,
            "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
            "env": {"PATH": path_env} if path_env else None,
            "description": "结构化渐进式思维工具 - 将复杂问题分解为可管理的步骤，支持修正和分支推理",
            "enabled": True,
        },
        {
            "name": "context7",
            "url": "https://mcp.context7.com/mcp",
            "transport": "http",
            "description": "实时库文档查询 - 获取最新的、版本特定的库文档和代码示例",
            "enabled": True,
        },
    ]


# ============== 公开 API ==============

# 开发环境默认 MCP Server 列表（模块级常量，惰性求值仅一次）
DEFAULT_MCP_SERVERS: list[dict[str, Any]] = _build_default_servers()


def get_system_mcp_servers() -> list[dict[str, Any]]:
    """获取系统级 MCP Server 列表。

    优先级：
        1. ``MCP_SYSTEM_SERVERS_JSON`` 环境变量（JSON 字符串，生产环境推荐）
        2. ``DEFAULT_MCP_SERVERS``（开发环境默认 2 个 server）

    Returns:
        系统 MCP Server 配置列表。生产环境未显式配置时返回空列表，
        应通过 ``MCPServerDiscovery.discover_from_registry()`` 从注册中心发现。

    Note:
        本函数取代原 ``getattr(django_settings, 'MCP_SERVERS', [])`` 的间接访问，
        配置归属业务模块而非 Django settings，符合"是什么就是什么"原则。
    """
    env_json = os.environ.get("MCP_SYSTEM_SERVERS_JSON")
    if env_json:
        try:
            servers = json.loads(env_json)
            if isinstance(servers, list):
                logger.debug("从 MCP_SYSTEM_SERVERS_JSON 环境变量加载 %d 个系统 MCP Server", len(servers))
                return servers
            logger.warning("MCP_SYSTEM_SERVERS_JSON 不是合法 JSON 数组，回退到默认列表")
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("MCP_SYSTEM_SERVERS_JSON 解析失败: %s，回退到默认列表", e)

    return DEFAULT_MCP_SERVERS
