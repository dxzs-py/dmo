import os
os.chdir(r'd:\programming\langchain\langchain_xm\backend\Django_xm')
from dotenv import load_dotenv
load_dotenv()
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Django_xm.settings')
import django
django.setup()

import asyncio
from django.conf import settings as django_settings

print("=" * 60)
print("MCP Tool Loading Debug")
print("=" * 60)

# 1. Check langchain-mcp-adapters
try:
    import langchain_mcp_adapters
    print(f"[OK] langchain-mcp-adapters installed")
except ImportError as e:
    print(f"[FAIL] langchain-mcp-adapters not installed: {e}")

# 2. Check MCP_SERVERS config
mcp_servers = getattr(django_settings, 'MCP_SERVERS', [])
print(f"\n[CONFIG] MCP_SERVERS count: {len(mcp_servers)}")
for srv in mcp_servers:
    enabled = srv.get('enabled', True)
    name = srv.get('name', 'unknown')
    transport = srv.get('transport', 'unknown')
    print(f"  - {name} (transport={transport}, enabled={enabled})")
    if transport == 'stdio':
        print(f"    command: {srv.get('command')} args: {srv.get('args')}")
    else:
        print(f"    url: {srv.get('url')}")

# 3. Test _get_mcp_servers_config (only enabled)
from Django_xm.apps.tools.mcp import _get_mcp_servers_config, is_mcp_available
enabled_servers = _get_mcp_servers_config()
print(f"\n[CONFIG] Enabled servers: {len(enabled_servers)}")
for s in enabled_servers:
    print(f"  - {s.get('name')}")

print(f"\n[CONFIG] MCP available: {is_mcp_available()}")

# 4. Test _load_mcp_tools_async directly
async def test_mcp_loading():
    from Django_xm.apps.tools import _load_mcp_tools_async, get_tools_for_request_async

    print("\n" + "=" * 60)
    print("Test: _load_mcp_tools_async (all servers)")
    print("=" * 60)
    tools = await _load_mcp_tools_async(selected_servers=None)
    print(f"Result: {len(tools)} tools loaded")
    for t in tools:
        print(f"  Tool: {t.name}")

    print("\n" + "=" * 60)
    print("Test: _load_mcp_tools_async (sequential-thinking only)")
    print("=" * 60)
    tools = await _load_mcp_tools_async(selected_servers=['sequential-thinking'])
    print(f"Result: {len(tools)} tools loaded")
    for t in tools:
        print(f"  Tool: {t.name}")

    print("\n" + "=" * 60)
    print("Test: _load_mcp_tools_async (context7 only)")
    print("=" * 60)
    tools = await _load_mcp_tools_async(selected_servers=['context7'])
    print(f"Result: {len(tools)} tools loaded")
    for t in tools:
        print(f"  Tool: {t.name}")

    print("\n" + "=" * 60)
    print("Test: get_tools_for_request_async (use_mcp=True)")
    print("=" * 60)
    tools = await get_tools_for_request_async(
        use_tools=True,
        use_advanced_tools=False,
        use_web_search=False,
        use_mcp=True,
        selected_mcp_servers=['sequential-thinking', 'context7'],
    )
    print(f"Result: {len(tools)} total tools")
    mcp_tools = [t for t in tools if hasattr(t, 'name') and 'mcp' in str(type(t)).lower() or 'sequential' in str(t.name).lower()]
    print(f"  MCP-related tools: {len(mcp_tools)}")

    print("\n" + "=" * 60)
    print("Test: get_tools_for_request_async (use_mcp=False)")
    print("=" * 60)
    tools_no_mcp = await get_tools_for_request_async(
        use_tools=True,
        use_advanced_tools=False,
        use_web_search=False,
        use_mcp=False,
    )
    print(f"Result: {len(tools_no_mcp)} total tools (without MCP)")

asyncio.run(test_mcp_loading())
print("\n[DONE] All MCP tests completed")
