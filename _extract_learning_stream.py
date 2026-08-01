"""Extract workflow_stream + async helpers from learning/views.py to services/learning_stream.py.

P1 #7 拆分：
- 将 workflow_stream（函数视图，L701）+ 4 个 async helpers（L919-1297）
  + 2 个共享辅助（_safe_publish_workflow_event L43、_WorkflowJSONEncoder L70，
  被流函数与视图共用）迁移到 services/learning_stream.py
- views.py 保留 12 个视图类，通过 re-import 复用共享辅助与 workflow_stream（URL 路由兼容）
- 更新 approval_gateway.py 的 _stream_learning_resume_generator lazy import

依赖方向（单向，无循环）：
    views.py → learning_stream.py（re-import 共享辅助 + workflow_stream + resume generator）
    learning_stream.py 不依赖 views.py（所有依赖来自 common/services/models）
"""
from pathlib import Path

BASE = Path(r"d:\programming\langchain\langchain_xm\backend\Django_xm\Django_xm\apps\learning")
VIEWS = BASE / "views.py"
NEW_MODULE = BASE / "services" / "learning_stream.py"
GATEWAY = Path(r"d:\programming\langchain\langchain_xm\backend\Django_xm\Django_xm\common\approval_gateway.py")

lines = VIEWS.read_text(encoding="utf-8").splitlines(keepends=True)

# 行号（1-indexed）→ 索引（0-indexed）
# L43-82: _safe_publish_workflow_event + _WorkflowJSONEncoder
# L85-700: 12 个视图类（保留）
# L701-1297: workflow_stream + 4 async helpers（迁移）
HELPERS_START, HELPERS_END = 42, 82      # 0-indexed [42, 82) → L43-L82
VIEWS_START, VIEWS_END = 84, 700          # L85-L700
EXTRACT_START, EXTRACT_END = 700, len(lines)  # L701-末尾

# 校验边界
assert lines[42].startswith("def _safe_publish_workflow_event("), f"L43: {lines[42]!r}"
assert lines[69].startswith("class _WorkflowJSONEncoder("), f"L70: {lines[69]!r}"
assert lines[84].startswith("class WorkflowStartView("), f"L85: {lines[84]!r}"
assert lines[700].startswith("def workflow_stream("), f"L701: {lines[700]!r}"
print(f"[OK] 边界校验通过：helpers L43-82, views L85-700, extracted L701-{len(lines)}")

helpers_block = lines[HELPERS_START:HELPERS_END]
extracted_block = lines[EXTRACT_START:EXTRACT_END]

# 构造 learning_stream.py：超集 imports（后续 ruff --fix 清理未使用）
new_module = '''"""学习工作流 SSE 流式生成器（从 views.py 抽离，P1 #7 拆分）。

职责：
    - workflow_stream：工作流进度 SSE 函数视图（注册于 learning/urls.py:stream）
    - _publish_learning_tool_event(s)：工具事件推送到 WebSocket（task 频道）
    - _detect_and_handle_learning_approval_interrupt：审批中断检测与处理
    - _stream_learning_resume_generator：学习审批恢复 SSE 异步生成器
    - _safe_publish_workflow_event / _WorkflowJSONEncoder：流式与视图共享辅助
      （views.py 通过 re-import 复用）

设计说明：
    - 原位于 ``views.py``，与视图层职责不同，故抽出独立服务模块。
    - 依赖方向单向：本模块不依赖 views.py，所有依赖来自 common/services/models，
      避免循环导入。views.py re-import 本模块的共享辅助与 workflow_stream。
"""

import json
import uuid
from urllib.parse import quote

from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from Django_xm.apps.core.config import get_logger
from Django_xm.apps.core.services.file_manager import get_file_manager
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.event_schema import EventSource, EventType
from Django_xm.common.permissions import IsAuthenticatedOrQueryParam
from Django_xm.common.realtime_events import publish_event_sync
from Django_xm.common.responses import error_response, not_found_response, success_response, validation_error_response
from Django_xm.common.serializers import EmptySerializer
from Django_xm.common.sse_utils import SSERenderer, sse_error_event, sse_response

from ..models import WorkflowSession
from ..serializers import (
    WorkflowResponseSerializer,
    WorkflowSessionSerializer,
    WorkflowStartSerializer,
    WorkflowSubmitSerializer,
)
from . import WorkflowService
from .resilience import stream_with_resilience
from .study_flow import _get_study_flow, get_workflow_state

logger = get_logger(__name__)
file_manager = get_file_manager()


'''
new_module += "".join(helpers_block)
# helpers_block 末尾是 L82，后面有空行 L83-84，再加一个空行分隔
new_module += "\n\n"
new_module += "".join(extracted_block)
if not new_module.endswith("\n"):
    new_module += "\n"

NEW_MODULE.write_text(new_module, encoding="utf-8")
print(f"[OK] 创建 {NEW_MODULE.name}（helpers {len(helpers_block)} 行 + extracted {len(extracted_block)} 行）")

# 重写 views.py：L1-42（imports + logger + file_manager）+ re-import 块 + L85-700（视图类）
head = lines[:42]  # L1-L42（含 docstring + imports + logger + file_manager + 空行）

reimport_block = '''
# ── 流式生成器与共享辅助（P1 #7 抽离至 services/learning_stream.py） ──
# Facade re-import：workflow_stream 仍由 urls.py 通过 views.workflow_stream 引用；
# _safe_publish_workflow_event / _WorkflowJSONEncoder 被本文件视图类与流式模块共用，
# 故 re-import 保持向后兼容；_stream_learning_resume_generator 供 approval_gateway 路由。
from .services.learning_stream import (
    _WorkflowJSONEncoder,
    _safe_publish_workflow_event,
    _stream_learning_resume_generator,
    workflow_stream,
)


'''

views_block = lines[VIEWS_START:VIEWS_END]  # L85-L700

new_views = "".join(head) + reimport_block + "".join(views_block)
if not new_views.endswith("\n"):
    new_views += "\n"
VIEWS.write_text(new_views, encoding="utf-8")
print(f"[OK] 重写 {VIEWS.name}（head {len(head)} 行 + re-import + views {len(views_block)} 行）")

# 更新 approval_gateway.py 的 lazy import
gw_text = GATEWAY.read_text(encoding="utf-8")
old_imp = "from Django_xm.apps.learning.views import _stream_learning_resume_generator"
new_imp = "from Django_xm.apps.learning.services.learning_stream import _stream_learning_resume_generator"
assert old_imp in gw_text, "approval_gateway.py 未找到旧 import"
gw_text = gw_text.replace(old_imp, new_imp)
GATEWAY.write_text(gw_text, encoding="utf-8")
print(f"[OK] 更新 {GATEWAY.name} 的 import")

print("\n提取完成。请运行：ruff --fix + manage.py check + pytest")
