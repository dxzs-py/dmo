import os, sys, json
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Django_xm.settings')
import django
django.setup()
from django.apps import apps

Approval = apps.get_model('approvals', 'Approval')
ChatMessage = apps.get_model('chat', 'ChatMessage')

# 查询测试审批记录
a = Approval.objects.filter(interrupt_id='call_00_WWbNgwumMd7vBSFLvWsA2192').first()
if a:
    print('APPROVAL_ID:', a.id)
    print('STATE:', a.state)
    print('CHAT_SESSION_ID:', repr(a.chat_session_id))
    print('SOURCE:', a.source)
    print('SOURCE_ID:', repr(a.source_id))
    print('TOOL_NAME:', a.tool_name)
    print('CREATED_AT:', a.created_at)
    print('EXTRA:', json.dumps(a.extra, ensure_ascii=False, indent=2) if a.extra else None)
else:
    print('APPROVAL NOT FOUND')

# 查询 MSG 2709 的创建时间和 tool_calls
msg = ChatMessage.objects.get(id=2709)
print('\nMSG_2709_CREATED_AT:', msg.created_at)
print('MSG_2709_TOOL_CALLS_COUNT:', len(msg.tool_calls or []))
print('MSG_2709_SESSION_ID:', msg.session_id)
