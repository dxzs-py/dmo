"""查本次任务所有 Approval extra 的 position/seq，确认 spawn 是否绑定。"""
import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
django.setup()

from Django_xm.apps.approvals.models import Approval

rows = Approval.objects.filter(source_id__startswith="research_1121b06ce357").order_by("created_at")
print("approval count:", rows.count())
for a in rows:
    extra = a.extra or {}
    print(
        f"  tool={a.tool_name} state={a.state} "
        f"pos={extra.get('position')} seq={extra.get('seq')} "
        f"tc_id={str(a.interrupt_id)[:36]}"
    )
