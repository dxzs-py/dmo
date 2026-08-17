import json
from django.apps import apps

RT = apps.get_model('research', 'ResearchTask')
CM = apps.get_model('chat', 'ChatMessage')

t = RT.objects.filter(task_id='research_2e86a7bc7454', is_deleted=False).first()
print('TASK', t.task_id, 'status', t.status, 'session_id', t.session_id)
print('RT content_len', len(t.content or ''))
print('RT content[:400]', repr((t.content or '')[:400]))
print('RT tool_calls_count', len(t.tool_calls or []))
for i, x in enumerate(t.tool_calls or []):
    print('TC', i, json.dumps({
        'name': x.get('name'),
        'seq': x.get('seq'),
        'position': x.get('position'),
        'subagent': x.get('subagent_thread_id'),
        'status': x.get('status'),
        'id': (x.get('id') or '')[:20],
    }, ensure_ascii=False))

m = None
if getattr(t, 'chat_message_id', None):
    m = CM.objects.filter(id=t.chat_message_id).first()
if m is None:
    m = CM.objects.filter(research_task_id=t.task_id).first()
if m:
    print('MSG id', m.id, 'role', m.role, 'content_len', len(m.content or ''))
    print('MSG content[:400]', repr((m.content or '')[:400]))
    print('MSG tool_calls_count', len(m.tool_calls or []))
    for i, x in enumerate(m.tool_calls or []):
        print('MTC', i, json.dumps({
            'name': x.get('name'),
            'seq': x.get('seq'),
            'position': x.get('position'),
            'subagent': x.get('subagent_thread_id'),
            'status': x.get('status'),
        }, ensure_ascii=False))
else:
    print('NO ChatMessage found')
