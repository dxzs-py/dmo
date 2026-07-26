import os, json, django
os.environ['DJANGO_SETTINGS_MODULE'] = 'Django_xm.settings.dev'
django.setup()

from django.apps import apps
ChatMessage = apps.get_model('chat', 'ChatMessage')

msgs = ChatMessage.objects.filter(role='assistant').order_by('-created_at')[:2]
for msg in msgs:
    print(f"\nmsg_id={msg.id}")
    tc = msg.tool_calls or []
    print(f"tool_calls count: {len(tc)}")
    if isinstance(tc, dict):
        tc = [tc]
    for i, t in enumerate(tc):
        print(f"  [{i}] name={t.get('name')}, status={t.get('status')}, approval={json.dumps(t.get('approval',{}))[:200]}")
