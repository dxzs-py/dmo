"""临时调试脚本：检查 ChatMessage 2401 的 tool_calls 审批状态"""
import os
import django

os.environ['DJANGO_SETTINGS_MODULE'] = 'Django_xm.settings.dev'
django.setup()

from Django_xm.apps.chat.models import ChatMessage

msg = ChatMessage.objects.get(id=2401)
tcs = msg.tool_calls or []
print(f'ChatMessage 2401: tool_calls count={len(tcs)}')
print(f'is_streaming={msg.is_streaming}')
print()
for i, tc in enumerate(tcs):
    name = tc.get('name', '')
    approval = tc.get('approval', {}) or {}
    state = approval.get('state', 'None')
    interrupt_id = approval.get('interrupt_id', 'None')
    params = tc.get('parameters', {})
    status = tc.get('status', 'None')
    print(f'  [{i}] name={name}, status={status}, approval.state={state}, interrupt_id={interrupt_id}')
    print(f'       parameters={params}')
    print()
