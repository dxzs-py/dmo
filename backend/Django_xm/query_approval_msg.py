import os, sys, json
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Django_xm.settings')
import django
django.setup()
from django.apps import apps

Approval = apps.get_model('approvals', 'Approval')
ChatMessage = apps.get_model('chat', 'ChatMessage')

# 查询新触发的 approval
interrupt_id = 'call_00_H7Sk9qCZX9CcuaSkX9TT5943'
a = Approval.objects.filter(interrupt_id=interrupt_id).first()
if a:
    print('=== Approval ===')
    print('APPROVAL_ID:', a.id)
    print('STATE:', a.state)
    print('CHAT_SESSION_ID:', repr(a.chat_session_id))
    print('SOURCE:', a.source)
    print('SOURCE_ID:', repr(a.source_id))
    print('TOOL_NAME:', a.tool_name)
    print('CREATED_AT:', a.created_at)
    extra = a.extra if isinstance(a.extra, dict) else {}
    print('EXTRA.message_id:', repr(extra.get('message_id')))
    print('EXTRA.tool_call_id:', repr(extra.get('tool_call_id')))
    print('EXTRA.graph_interrupt_id:', repr(extra.get('graph_interrupt_id')))
else:
    print('APPROVAL NOT FOUND')

# 查询 msg=2543 和 msg=2713 的 tool_calls
for msg_id in [2543, 2713]:
    try:
        msg = ChatMessage.objects.get(id=msg_id)
        print(f'\n=== MSG {msg_id} ===')
        print('SESSION_ID:', msg.session_id)
        print('ROLE:', msg.role)
        print('CREATED_AT:', msg.created_at)
        print('IS_STREAMING:', msg.is_streaming)
        tcs = msg.tool_calls or []
        print('TOOL_CALLS_COUNT:', len(tcs))
        for i, tc in enumerate(tcs):
            if not isinstance(tc, dict):
                continue
            tc_id = tc.get('id') or tc.get('tool_call_id') or ''
            appr = tc.get('approval') or {}
            print(f'  TC[{i}]: id={tc_id}, name={tc.get("name")}, '
                  f'approval_state={appr.get("state")}, '
                  f'approval_title={appr.get("title")}, '
                  f'approval_operation={appr.get("operation")}')
    except ChatMessage.DoesNotExist:
        print(f'\nMSG {msg_id} NOT FOUND')

# 查询 session 内最新的 assistant 消息
session_id = '00fff590-e814-4986-bcec-72490b5a1bc7'
msgs = ChatMessage.objects.filter(
    session__session_id=session_id,
    role='assistant',
    is_deleted=False,
).order_by('-created_at')[:3]
print(f'\n=== Session {session_id} latest 3 assistant messages ===')
for m in msgs:
    tcs = m.tool_calls or []
    has_approval = any(isinstance(tc, dict) and tc.get('approval') for tc in tcs)
    print(f'MSG {m.id}: created_at={m.created_at}, tool_calls={len(tcs)}, has_approval={has_approval}')
