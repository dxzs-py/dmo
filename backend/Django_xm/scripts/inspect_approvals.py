"""临时诊断脚本：检查最近 chat 审批的 extra 字段持久化情况。"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings")
django.setup()

from Django_xm.apps.approvals.models import Approval

qs = Approval.objects.filter(source="chat").order_by("-created_at")[:5]
print(f"共 {qs.count()} 条最近 chat 审批")
print("=" * 80)
for a in qs:
    extra = a.extra if isinstance(a.extra, dict) else {}
    print(f"id={a.id} state={a.state} tool={a.tool_name}")
    print(f"  interrupt_id={a.interrupt_id!r}")
    print(f"  extra.langgraph_resume_id={extra.get('langgraph_resume_id')!r}")
    print(f"  extra.graph_interrupt_id={extra.get('graph_interrupt_id')!r}")
    print(f"  extra.tool_call_id={extra.get('tool_call_id')!r}")
    print("-" * 40)
