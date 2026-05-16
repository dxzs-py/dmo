import django
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Django_xm.settings.dev')
django.setup()

from Django_xm.apps.tools.mcp import is_mcp_available, _get_mcp_servers_config, get_server_info_list

print('MCP 可用:', is_mcp_available())
servers = _get_mcp_servers_config()
print('已配置服务器:', len(servers))
for s in servers:
    name = s.get('name', 'unknown')
    transport = s.get('transport', 'sse')
    enabled = s.get('enabled', True)
    print(f'  - {name} ({transport}) enabled={enabled}')

info = get_server_info_list()
for i in info:
    print(f'  服务器信息: {i}')

print('\n--- 测试本地 MCP 工具 ---')
from Django_xm.apps.tools.mcp.local import get_local_mcp_tools
local_tools = get_local_mcp_tools()
print(f'本地工具数量: {len(local_tools)}')
for t in local_tools:
    print(f'  - {t.name}: {t.description[:50]}')

print('\n--- 测试本地工具执行 ---')
for t in local_tools:
    if t.name == 'project_info':
        result = t._run(query='技术栈')
        print(f'  project_info(技术栈): {result[:100]}')
    elif t.name == 'system_status':
        result = t._run(check_type='database')
        print(f'  system_status(database): {result}')

print('\n--- 测试中间件 ---')
from Django_xm.apps.tools.mcp.middleware import get_tool_registry, get_tool_call_log
registry = get_tool_registry()
for t in local_tools:
    registry.register(t, source='local')
print(f'注册工具数量: {len(registry.list_tools())}')
for info in registry.list_tools():
    print(f'  - {info["name"]} (来源: {info["source"]})')

print('\n所有测试通过!')
