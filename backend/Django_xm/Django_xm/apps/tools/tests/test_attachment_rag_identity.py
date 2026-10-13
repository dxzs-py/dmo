"""C-1（P1 多租户安全）：AttachmentRagSearchTool 单例 thread_id 身份传递测试。

缺陷（修复前）：模块级单例 attachment_rag_search 被有附件的请求共享注入
（tool_service.py 追加对象引用），thread_id 是实例可变字段，每次 invoke/ainvoke
前由 _capture_thread_id 写入 self.thread_id；并发会话下 B 会话的 capture 覆写
A 已捕获的值（capture 与 _run 之间隔着 super().ainvoke 回调调度与
sync_to_async 线程切换），A 的检索以 B 的 session 执行 → B 的附件文本进入
A 的 LLM 上下文。审批策略 SAFE 自动通过（policies.py AttachmentRagSearchApprovalPolicy），
泄漏面无人工闸门。

修复契约（本文件锁定，B1 同款身份传递模式）：
1. thread_id 不再是实例字段（无跨调用可变状态）；
2. invoke/ainvoke 边界从本次调用的 config.configurable 捕获 → ContextVar
   （任务级上下文；asgiref sync_to_async 复制上下文进工作线程）→ _run 读取；
3. 图内直接 _run 调用回退 langgraph.config.get_config()（运行期 config，
   与 termination_judge 身份取值同口径）；图外无身份 → "无法确定当前会话"；
4. 并发回归：两会话交错 ainvoke，检索各自归属 session，互不串扰。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.tools.tests.test_attachment_rag_identity --noinput
"""

import asyncio
import os
import uuid

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import SimpleTestCase

from Django_xm.apps.tools.langchain.attachment_rag import attachment_rag_search


def _unique_session(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class InterleavedIdentityTests(SimpleTestCase):
    """并发回归（判别用例，修复前红）：两会话交错 capture/_run 互不串扰。

    _run 在无 DB 的测试环境下命中"查找会话失败"分支，返回值内嵌实际使用的
    session_id——因此可直接断言各协程结果归属各自会话。
    """

    def test_interleaved_ainvoke_results_isolated(self):
        session_a = _unique_session("sess-a")
        session_b = _unique_session("sess-b")

        async def scenario():
            async def run_for(thread_id: str) -> str:
                return await attachment_rag_search.ainvoke(
                    "检索测试", config={"configurable": {"thread_id": thread_id}}
                )

            # gather 将两个协程包成独立 Task（各自上下文）；A 在 capture 后挂起于
            # sync_to_async 执行器，B 的 capture 随后执行——修复前此处覆写实例字段
            return await asyncio.gather(run_for(session_a), run_for(session_b))

        result_a, result_b = asyncio.run(scenario())
        self.assertIn(session_a, result_a)
        self.assertIn(session_b, result_b)

    def test_invoke_does_not_mutate_singleton_state(self):
        """修复后单例无 thread_id 实例字段（无跨调用可变状态）。

        修复前：pydantic 字段恒存在且 capture 后携带会话值 → 本用例红。
        """
        session = _unique_session("sess-state")
        attachment_rag_search.invoke("检索测试", config={"configurable": {"thread_id": session}})
        self.assertFalse(
            hasattr(attachment_rag_search, "thread_id"),
            "单例不得携带 thread_id 实例状态（C-1 跨会话竞态根源）",
        )


class IdentityResolutionTests(SimpleTestCase):
    """身份解析契约：config 透传 ✓ / 图外无身份 → 明确文案。"""

    def test_sync_invoke_resolves_session_from_config(self):
        """同步 invoke：thread_id 从本次 config 捕获并落入检索结果。"""
        session = _unique_session("sess-sync")
        result = attachment_rag_search.invoke("检索测试", config={"configurable": {"thread_id": session}})
        self.assertIn(session, result)

    def test_run_without_identity_reports_session_unknown(self):
        """图外直接 _run（无 config、无图上下文）→ 返回明确提示而非错误。"""
        result = attachment_rag_search._run("检索测试")
        self.assertIn("无法确定当前会话", result)

    def test_ainvoke_without_config_reports_session_unknown(self):
        result = asyncio.run(attachment_rag_search.ainvoke("检索测试"))
        self.assertIn("无法确定当前会话", result)
