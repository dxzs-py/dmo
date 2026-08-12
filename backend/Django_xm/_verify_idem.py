"""临时验证：middleware 幂等构建（验证后删除）"""
import django
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
django.setup()

from langchain_core.messages import AIMessage

from Django_xm.apps.approvals.models import Approval
from Django_xm.common.tool_call_lifecycle import service

last_ai_msg = AIMessage(
    content="",
    tool_calls=[
        {"id": "call_00_Y8aqNoeFQ4xd7yhoc9a37194", "name": "write_todos", "args": {"todos": []}},
        {"id": "call_01_FiWOPiGNc0ZJeELH1i9w8729", "name": "ls", "args": {"path": "/"}},
    ],
)

_all_tc_ids = [t.get("id", "") for t in last_ai_msg.tool_calls]
_resolved_ids = set(
    Approval.objects.filter(interrupt_id__in=_all_tc_ids)
    .exclude(state=Approval.STATE_PENDING)
    .values_list("interrupt_id", flat=True)
)
print("_all_tc_ids:", _all_tc_ids)
print("_resolved_ids:", _resolved_ids)

_idempotent_tc_ids = set()
for _t in last_ai_msg.tool_calls:
    _tid = _t.get("id", "")
    try:
        _ctx = service.get_context(_tid)
        _last_evt = _ctx.get("last_event_type") if _ctx else None
        print(f"  ctx[{_tid}]: {_last_evt!r}")
        if _tid in _resolved_ids or _last_evt in ("tool_call_completed", "tool_call_failed"):
            _idempotent_tc_ids.add(_tid)
    except Exception as e:
        print(f"  ctx[{_tid}] ERROR: {e!r}")
print("_idempotent_tc_ids:", _idempotent_tc_ids)
