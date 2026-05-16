import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'Django_xm.settings.dev'
import django
django.setup()

import asyncio

async def test():
    from Django_xm.apps.tools import get_tools_for_request_async
    tools = await get_tools_for_request_async(
        use_tools=True,
        use_advanced_tools=False,
        use_web_search=False,
        use_mcp=True,
    )
    print(f"Total tools: {len(tools)}")
    for t in tools:
        desc = (t.description or "")[:60]
        print(f"  - {t.name}: {desc}")

asyncio.run(test())
