import django
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Django_xm.settings.dev')
django.setup()

import asyncio

async def test_context7():
    print('=== 测试 context7 远程 MCP 服务器 (HTTP) ===')
    from Django_xm.apps.tools.mcp import get_mcp_tools
    try:
        tools = await get_mcp_tools(
            server_name='context7',
            transport='http',
        )
        print(f'context7 工具数量: {len(tools)}')
        for t in tools:
            desc = t.description[:80] if t.description else ''
            print(f'  - {t.name}: {desc}')
        return True
    except Exception as e:
        print(f'context7 连接失败: {e}')
        return False

async def test_sequential_thinking():
    print('\n=== 测试 Sequential Thinking MCP 服务器 (stdio) ===')
    from Django_xm.apps.tools.mcp import get_mcp_tools
    try:
        tools = await get_mcp_tools(
            server_name='sequential-thinking',
            transport='stdio',
            command='npx',
            args=['-y', '@modelcontextprotocol/server-sequential-thinking'],
        )
        print(f'sequential-thinking 工具数量: {len(tools)}')
        for t in tools:
            desc = t.description[:80] if t.description else ''
            print(f'  - {t.name}: {desc}')
        return True
    except Exception as e:
        print(f'sequential-thinking 连接失败: {e}')
        return False

async def main():
    r1 = await test_context7()
    r2 = await test_sequential_thinking()

    print('\n=== 测试结果 ===')
    print(f'context7: {"✅ 通过" if r1 else "❌ 失败"}')
    print(f'sequential-thinking: {"✅ 通过" if r2 else "❌ 失败"}')

    from Django_xm.apps.tools.mcp import cleanup_mcp_clients
    await cleanup_mcp_clients()

asyncio.run(main())
