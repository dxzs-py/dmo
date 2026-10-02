"""study_flow / workflow_service（学习工作流编排）测试（ARCH-21）。

覆盖：
- StudyFlow 初始化：显式 checkpointer 注入、默认 get_checkpointer 同步入口表征测试
- 图拓扑 + 中断恢复：MemorySaver + mock 节点，全流程 start → 中断 → 提交恢复 → 完成
- invoke / ainvoke / stream 的 thread_id 配置注入
- 进程内缓存：命中 / TTL 过期 / LRU 淘汰 / 失效清理
- start_study_flow：会话预创建、事件发布（开始/等待/完成/失败）、持久化调用
- submit_answers：提交守卫（WorkflowAlreadyFinishedError）、状态恢复、无状态报错
- get_workflow_state / get_workflow_history：内存命中 / 持久化回退
- prepare_restart：参数校验、新会话预创建、计划复用
- _persist_attempt_and_lock_questions / _update_workflow_session_tokens（DB）
- WorkflowService：start/submit 结果映射、delete_workflow、list_user_workflows

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.learning.tests.test_study_flow --noinput
"""

import asyncio
import os
import time
import uuid
from unittest import TestCase as UnitTestCase
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

# ── 循环导入污染修复（仅测试进程内，生产代码待修）─────────────────────────
# 潜在问题链：services/__init__.py 第 8 行 `from .study_flow import _get_study_flow`
# 会在 nodes 包初始化期间被触发（nodes/feedback_node → services._model_helper
# → services/__init__ → study_flow），此时 nodes 仅完成子模块 setattr（属性为
# module），study_flow 的 `from ..nodes import planner_node, ...` 拿到的是
# module 而非函数 → StudyFlow._build_graph 报 TypeError。
# 触发条件：nodes 子模块先于 services 包被导入（测试套件按字母序发现即触发）。
# 此处将 study_flow 命名空间内的节点引用重绑定为真实函数，恢复预期行为；
# 根因修复应调整 services/__init__.py 的导出或 study_flow 的节点导入时机。
from types import ModuleType

from django.contrib.auth import get_user_model
from django.test import TestCase
from langgraph.checkpoint.memory import MemorySaver

from Django_xm.apps.learning import nodes as _nodes_pkg
from Django_xm.apps.learning.models import (
    WorkflowAttempt,
    WorkflowQuestion,
    WorkflowQuestionStatus,
    WorkflowSession,
)
from Django_xm.apps.learning.services import study_flow as sf
from Django_xm.apps.learning.services.study_flow import (
    StudyFlow,
    WorkflowAlreadyFinishedError,
    _ensure_session_created,
    _get_cached_study_flow,
    _invalidate_study_flow_cache,
    _is_cache_entry_valid,
    _persist_attempt_and_lock_questions,
    _study_flow_cache,
    _update_workflow_session_tokens,
    get_workflow_history,
    get_workflow_state,
    prepare_restart,
    start_study_flow,
    submit_answers,
)
from Django_xm.apps.learning.services.workflow_service import WorkflowService

for _name in ("planner_node", "retrieval_node", "quiz_generator_node", "grading_node", "feedback_node"):
    _current = getattr(sf, _name, None)
    if isinstance(_current, ModuleType):
        setattr(sf, _name, getattr(_nodes_pkg, _name))
del _current, _name

User = get_user_model()

_SF = "Django_xm.apps.learning.services.study_flow"
# workflow_service 将 study_flow 函数导入自身命名空间，patch 须指向其命名空间
_WS = "Django_xm.apps.learning.services.workflow_service"


def _clear_cache():
    """清空进程内 StudyFlow 缓存（测试隔离）"""
    _study_flow_cache.clear()


def _make_quiz() -> dict:
    """最小练习题结构"""
    return {
        "questions": [
            {"id": "q1", "type": "multiple_choice", "question": "1+1=?", "options": ["1", "2"], "answer": "2",
             "explanation": "", "points": 10}
        ],
        "total_points": 10,
        "time_limit": 5,
    }


class StudyFlowInitTests(UnitTestCase):
    """StudyFlow 初始化与 checkpointer 注入"""

    def setUp(self):
        _clear_cache()
        self.addCleanup(_clear_cache)

    def test_explicit_checkpointer_used_directly(self):
        """显式传入 checkpointer 时不触发 get_checkpointer"""
        sentinel = MemorySaver()
        with mock.patch(f"{_SF}.get_checkpointer") as factory:
            flow = StudyFlow(thread_id="t-init", checkpointer=sentinel)
        factory.assert_not_called()
        self.assertIs(flow.checkpointer, sentinel)

    def test_default_checkpointer_sync_entry(self):
        """表征测试：checkpointer=None 时同步调用 get_checkpointer()（无参）。

        合并统一 checkpointer（同步 get_checkpointer → get_async_checkpointer）
        时需更新本表征测试。
        """
        sentinel = MemorySaver()
        with mock.patch(f"{_SF}.get_checkpointer", return_value=sentinel) as factory:
            flow = StudyFlow(thread_id="t-init2")
        factory.assert_called_once_with()
        self.assertIs(flow.checkpointer, sentinel)

    def test_graph_contains_expected_nodes(self):
        """图包含全部节点与入口（拓扑防回归）"""
        flow = StudyFlow(thread_id="t-topo", checkpointer=MemorySaver())
        nodes = set(flow.graph.get_graph().nodes)
        self.assertEqual(
            nodes,
            {"__start__", "__end__", "planner", "retrieval", "quiz_generator", "human_review", "grading",
             "feedback"},
        )


class StudyFlowGraphIntegrationTests(UnitTestCase):
    """图拓扑 + 中断恢复全流程（mock 节点，MemorySaver 持久化）

    注意：图在 compile 时绑定节点函数引用，节点 mock 必须先于 StudyFlow
    构造生效；运行期行为变化通过实例属性开关控制，而非事后重新 patch。
    """

    def setUp(self):
        _clear_cache()
        self.addCleanup(_clear_cache)
        plan = {"topic": "闭包", "difficulty": "beginner", "key_points": ["a", "b"]}
        self.feedback_should_retry = False

        def _planner(state):
            return {"learning_plan": plan, "current_step": "planner_completed"}

        def _retrieval(state):
            return {"retrieved_docs": [], "current_step": "retrieval_completed"}

        def _quiz(state):
            # 重试轮次（retry_count > 0）返回不同总分，用于证明走到了新的出题分支
            total = 20 if state.get("retry_count", 0) > 0 else 10
            quiz = _make_quiz()
            quiz["total_points"] = total
            return {"quiz": quiz, "current_step": "waiting_for_answers"}

        def _grading(state):
            return {"score": 80, "current_step": "grading_completed"}

        def _feedback(state):
            should_retry = self.feedback_should_retry
            retry_count = state.get("retry_count", 0) + 1 if should_retry else state.get("retry_count", 0)
            return {"feedback": "反馈", "should_retry": should_retry, "retry_count": retry_count,
                    "current_step": "feedback_completed"}

        patches = [
            mock.patch(f"{_SF}.planner_node", _planner),
            mock.patch(f"{_SF}.retrieval_node", _retrieval),
            mock.patch(f"{_SF}.quiz_generator_node", _quiz),
            mock.patch(f"{_SF}.grading_node", _grading),
            mock.patch(f"{_SF}.feedback_node", _feedback),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        self.flow = StudyFlow(thread_id="t-graph", checkpointer=MemorySaver())

    def _start(self, thread_id: str = "t-graph"):
        """启动工作流并推进到中断点（human_review 之前）"""
        initial = {
            "messages": [],
            "user_question": "什么是闭包",
            "current_step": "start",
            "retry_count": 0,
            "should_retry": False,
            "thread_id": thread_id,
        }
        return self.flow.invoke(initial)

    def test_interrupt_and_resume_lifecycle(self):
        """启动后中断在 waiting_for_answers；提交答案恢复执行至 feedback_completed"""
        result = self._start()
        self.assertEqual(result["current_step"], "waiting_for_answers")
        self.assertEqual(result["quiz"]["total_points"], 10)

        # 中断恢复前 state 已落 checkpoint
        state = self.flow.get_state("t-graph")
        self.assertEqual(state.values["current_step"], "waiting_for_answers")

        # 提交答案并恢复：human_review → grading → feedback（should_retry=False → END）
        self.flow.update_state("t-graph", {"user_answers": {"q1": "2"}})
        final = self.flow.invoke(None)
        self.assertEqual(final["current_step"], "feedback_completed")
        self.assertEqual(final["score"], 80)

    def test_retry_loop_back_to_quiz(self):
        """should_retry=True 时条件边回到 quiz_generator（retry 分支，总分变为重试口径）"""
        self._start()
        self.feedback_should_retry = True
        self.flow.update_state("t-graph", {"user_answers": {"q1": "2"}})
        final = self.flow.invoke(None)
        # feedback → should_continue → retry → quiz_generator → human_review 再次中断
        self.assertEqual(final["current_step"], "waiting_for_answers")
        self.assertEqual(final["retry_count"], 1)
        self.assertEqual(final["quiz"]["total_points"], 20)  # 重试轮次的新题
        self.assertEqual(final["user_answers"], {"q1": "2"})


class StudyFlowInvokeConfigTests(UnitTestCase):
    """invoke / ainvoke / stream：thread_id 注入 config.configurable"""

    def test_invoke_injects_thread_id(self):
        """invoke 未传 config 时自动注入 thread_id"""
        flow = StudyFlow(thread_id="t-cfg", checkpointer=MemorySaver())
        with mock.patch(f"{_SF}.invoke_with_resilience", return_value={}) as invoke:
            flow.invoke({"a": 1})
        config = invoke.call_args[0][2]
        self.assertEqual(config["configurable"]["thread_id"], "t-cfg")

    def test_invoke_merges_existing_config(self):
        """已传 config 时合并 configurable（不覆盖其他键）"""
        flow = StudyFlow(thread_id="t-cfg", checkpointer=MemorySaver())
        with mock.patch(f"{_SF}.invoke_with_resilience", return_value={}) as invoke:
            flow.invoke({"a": 1}, config={"callbacks": []})
        config = invoke.call_args[0][2]
        self.assertEqual(config["configurable"]["thread_id"], "t-cfg")
        self.assertIn("callbacks", config)

    def test_ainvoke_injects_thread_id(self):
        """ainvoke 注入 thread_id"""
        flow = StudyFlow(thread_id="t-cfg2", checkpointer=MemorySaver())
        with mock.patch(f"{_SF}.ainvoke_with_resilience", new=mock.AsyncMock(return_value={})) as ainvoke:
            asyncio.run(flow.ainvoke({}))
        config = ainvoke.call_args[0][2]
        self.assertEqual(config["configurable"]["thread_id"], "t-cfg2")

    def test_stream_injects_thread_id(self):
        """stream 注入 thread_id 与 stream_mode"""
        flow = StudyFlow(thread_id="t-cfg3", checkpointer=MemorySaver())
        with mock.patch(f"{_SF}.stream_with_resilience", return_value=iter([])) as stream:
            list(flow.stream({}, stream_mode="updates"))
        config = stream.call_args[0][2]
        self.assertEqual(config["configurable"]["thread_id"], "t-cfg3")
        self.assertEqual(stream.call_args.kwargs["stream_mode"], "updates")

    def test_get_state_without_thread_raises(self):
        """get_state / update_state 缺 thread_id → ValueError"""
        flow = StudyFlow(thread_id=None, checkpointer=MemorySaver())
        with self.assertRaises(ValueError):
            flow.get_state()
        with self.assertRaises(ValueError):
            flow.update_state("", {})


class StudyFlowCacheTests(UnitTestCase):
    """StudyFlow 进程内缓存：命中 / 过期 / LRU / 失效"""

    def setUp(self):
        _clear_cache()
        self.addCleanup(_clear_cache)

    def test_cache_hit_returns_same_instance(self):
        """同 thread_id 二次获取返回同一实例"""
        with mock.patch(f"{_SF}.get_checkpointer", return_value=MemorySaver()):
            first = _get_cached_study_flow("t-cache")
            second = _get_cached_study_flow("t-cache")
        self.assertIs(first, second)

    def test_expired_entry_evicted(self):
        """TTL 过期条目被淘汰并重建"""
        with mock.patch(f"{_SF}.get_checkpointer", return_value=MemorySaver()):
            first = _get_cached_study_flow("t-exp")
            _study_flow_cache["t-exp"] = (first, time.time() - sf._STUDY_FLOW_CACHE_TTL - 1)
            second = _get_cached_study_flow("t-exp")
        self.assertIsNot(first, second)

    def test_is_cache_entry_valid(self):
        """缓存条目有效性判定：长度 / TTL"""
        self.assertFalse(_is_cache_entry_valid((object(),)))
        self.assertTrue(_is_cache_entry_valid((object(), time.time())))
        self.assertFalse(_is_cache_entry_valid((object(), time.time() - sf._STUDY_FLOW_CACHE_TTL - 1)))

    def test_lru_eviction_on_overflow(self):
        """缓存超过容量 → 最旧条目被淘汰"""
        with mock.patch(f"{_SF}.get_checkpointer", return_value=MemorySaver()), mock.patch.object(
            sf, "_STUDY_FLOW_CACHE_MAXSIZE", 2
        ):
            _get_cached_study_flow("t-lru-1")
            _get_cached_study_flow("t-lru-2")
            _get_cached_study_flow("t-lru-3")
        self.assertNotIn("t-lru-1", _study_flow_cache)
        self.assertIn("t-lru-3", _study_flow_cache)
        self.assertLessEqual(len(_study_flow_cache), 2)

    def test_invalidate_clears_process_cache(self):
        """失效清理移除进程内条目"""
        with mock.patch(f"{_SF}.get_checkpointer", return_value=MemorySaver()):
            _get_cached_study_flow("t-inv")
            _invalidate_study_flow_cache("t-inv")
        self.assertNotIn("t-inv", _study_flow_cache)


class StartStudyFlowTests(TestCase):
    """start_study_flow：会话预创建 + 事件发布 + 持久化"""

    def setUp(self):
        _clear_cache()
        self.addCleanup(_clear_cache)
        self.user = User.objects.create_user(username="sf-start", password="pw123456")

    def _patch_flow(self, invoke_result=None, invoke_error=None):
        """打桩 _get_cached_study_flow / publish_event_sync / persistence / token 统计"""
        flow = mock.Mock()
        if invoke_error is not None:
            flow.invoke.side_effect = invoke_error
        else:
            flow.invoke.return_value = invoke_result
        patches = [
            mock.patch(f"{_SF}._get_cached_study_flow", return_value=flow),
            mock.patch(f"{_SF}.publish_event_sync"),
            mock.patch(f"{_SF}.persistence_service"),
            mock.patch(f"{_SF}._update_workflow_session_tokens"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return flow

    def test_happy_path_creates_session_and_publishes(self):
        """正常启动：预创建 session + 持久化调用（事件发布细节见后续专项用例）"""
        result_state = {"current_step": "waiting_for_answers", "quiz": _make_quiz()}
        self._patch_flow(invoke_result=result_state)

        result = start_study_flow(
            "什么是闭包",
            "t-start-1",
            user_id=self.user.id,
            knowledge_base_ids=["kb1"],
            provider_id="openai",
            model_name="gpt-4o-mini",
        )
        self.assertEqual(result["current_step"], "waiting_for_answers")

        session = WorkflowSession.objects.get(thread_id="t-start-1")
        self.assertEqual(session.user_question, "什么是闭包")
        self.assertEqual(session.created_by_id, self.user.id)
        self.assertEqual(session.knowledge_base_ids, ["kb1"])
        self.assertEqual(session.provider_id, "openai")
        self.assertEqual(session.current_step, "start")

    def test_waiting_state_publishes_state_update(self):
        """暂停在 waiting_for_answers → 发布 WORKFLOW_STATE_UPDATE"""
        self._patch_flow(invoke_result={"current_step": "waiting_for_answers"})
        publish_mock = mock.patch(f"{_SF}.publish_event_sync").start()
        self.addCleanup(mock.patch.stopall)
        start_study_flow("Q", "t-start-2", user_id=self.user.id)
        event_types = [call.args[0] for call in publish_mock.call_args_list]
        self.assertIn(sf.EventType.WORKFLOW_STATE_UPDATE, event_types)

    def test_completed_state_publishes_completed(self):
        """非等待步骤 → 发布 WORKFLOW_COMPLETED"""
        self._patch_flow(invoke_result={"current_step": "feedback_completed"})
        publish_mock = mock.patch(f"{_SF}.publish_event_sync").start()
        self.addCleanup(mock.patch.stopall)
        start_study_flow("Q", "t-start-3", user_id=self.user.id)
        event_types = [call.args[0] for call in publish_mock.call_args_list]
        self.assertIn(sf.EventType.WORKFLOW_COMPLETED, event_types)

    def test_failure_publishes_failed_and_reraises(self):
        """图执行失败 → 发布 WORKFLOW_FAILED 并向上抛出"""
        self._patch_flow(invoke_error=RuntimeError("graph boom"))
        publish_mock = mock.patch(f"{_SF}.publish_event_sync").start()
        self.addCleanup(mock.patch.stopall)
        with self.assertRaises(RuntimeError):
            start_study_flow("Q", "t-start-4", user_id=self.user.id)
        event_types = [call.args[0] for call in publish_mock.call_args_list]
        self.assertIn(sf.EventType.WORKFLOW_FAILED, event_types)
        self.assertIn(sf.EventType.WORKFLOW_STEP, event_types)  # 开始事件已发布

    def test_persistence_called_with_result(self):
        """成功后调用 save_workflow_state（先持久化再更新 token）"""
        flow = self._patch_flow(invoke_result={"current_step": "waiting_for_answers"})
        start_study_flow("Q", "t-start-5", user_id=self.user.id)
        sf.persistence_service.save_workflow_state.assert_called_once()
        args = sf.persistence_service.save_workflow_state.call_args[0]
        self.assertEqual(args[0], "t-start-5")
        self.assertEqual(args[1]["current_step"], "waiting_for_answers")
        self.assertEqual(args[2], self.user.id)
        sf._update_workflow_session_tokens.assert_called_once()
        flow.invoke.assert_called_once()

    def test_ensure_session_created_idempotent(self):
        """_ensure_session_created 幂等：重复调用单行"""
        _ensure_session_created(
            thread_id="t-ensure",
            user_question="Q1",
            user_id=self.user.id,
            knowledge_base_ids=["kb"],
            provider_id="openai",
            model_name="m1",
            temperature=0.5,
            max_tokens=100,
            special_params={"thinking": True},
        )
        _ensure_session_created(
            thread_id="t-ensure",
            user_question="Q1",
            user_id=self.user.id,
            knowledge_base_ids=["kb"],
            provider_id="openai",
            model_name="m1",
            temperature=0.5,
            max_tokens=100,
            special_params={"thinking": True},
        )
        self.assertEqual(WorkflowSession.objects.filter(thread_id="t-ensure").count(), 1)
        session = WorkflowSession.objects.get(thread_id="t-ensure")
        self.assertTrue(session.enable_deep_thinking is False)
        self.assertEqual(session.special_params, {"thinking": True})


class SubmitAnswersTests(TestCase):
    """submit_answers：提交守卫与状态恢复"""

    def setUp(self):
        _clear_cache()
        self.addCleanup(_clear_cache)
        self.user = User.objects.create_user(username="sf-submit", password="pw123456")

    def test_finished_workflow_rejected(self):
        """工作流已结束（feedback_completed）→ WorkflowAlreadyFinishedError + current_phase"""
        flow = mock.Mock()
        flow.get_state.return_value = mock.Mock(values={"current_step": "feedback_completed"})
        with mock.patch(f"{_SF}._get_cached_study_flow", return_value=flow), self.assertRaises(
            WorkflowAlreadyFinishedError
        ) as ctx:
            submit_answers("t-sub-1", {"q1": "2"}, user_id=self.user.id)
        self.assertEqual(ctx.exception.current_phase, "feedback_completed")
        flow.invoke.assert_not_called()

    def test_no_state_anywhere_raises_value_error(self):
        """内存与持久化均无状态 → ValueError"""
        flow = mock.Mock()
        flow.get_state.return_value = None
        with mock.patch(f"{_SF}._get_cached_study_flow", return_value=flow), mock.patch(
            f"{_SF}.persistence_service"
        ) as persistence, self.assertRaises(ValueError):
            persistence.load_workflow_state.return_value = None
            submit_answers("t-sub-2", {"q1": "2"}, user_id=self.user.id)

    def test_restores_state_from_persistence(self):
        """内存无状态 → 从持久化恢复后继续执行"""
        flow = mock.Mock()
        empty_state = mock.Mock(values=None)
        restored_state = mock.Mock(values={"current_step": "waiting_for_answers"})
        flow.get_state.side_effect = [empty_state, restored_state]
        saved_state = {"current_step": "waiting_for_answers", "quiz": _make_quiz()}
        with mock.patch(f"{_SF}._get_cached_study_flow", return_value=flow), mock.patch(
            f"{_SF}.persistence_service"
        ) as persistence, mock.patch(f"{_SF}.publish_event_sync"), mock.patch(
            f"{_SF}.get_workflow_state", return_value={"current_step": "feedback_completed", "score": 90}
        ), mock.patch(f"{_SF}._persist_attempt_and_lock_questions"), mock.patch(
            f"{_SF}._update_workflow_session_tokens"
        ):
            persistence.load_workflow_state.return_value = saved_state
            result = submit_answers("t-sub-3", {"q1": "2"}, user_id=self.user.id)
        # 恢复时把 saved_state 写回 graph
        flow.graph.update_state.assert_called_once()
        self.assertEqual(flow.graph.update_state.call_args.kwargs["values"], saved_state)
        # 恢复执行以 None 触发（checkpoint 续跑）
        flow.invoke.assert_called_once()
        self.assertIsNone(flow.invoke.call_args[0][0])
        self.assertEqual(result["score"], 90)

    def test_disallowed_step_raises_with_phase(self):
        """未知结束态步骤同样被守卫拦截"""
        flow = mock.Mock()
        flow.get_state.return_value = mock.Mock(values={"current_step": "quiz_error"})
        with mock.patch(f"{_SF}._get_cached_study_flow", return_value=flow), self.assertRaises(
            WorkflowAlreadyFinishedError
        ) as ctx:
            submit_answers("t-sub-4", {"q1": "2"})
        self.assertEqual(ctx.exception.current_phase, "quiz_error")

    def test_allowed_waiting_step_proceeds(self):
        """waiting_for_answers 属允许提交步骤 → 执行恢复流程"""
        flow = mock.Mock()
        flow.get_state.return_value = mock.Mock(values={"current_step": "waiting_for_answers"})
        with mock.patch(f"{_SF}._get_cached_study_flow", return_value=flow), mock.patch(
            f"{_SF}.publish_event_sync"
        ), mock.patch(f"{_SF}.persistence_service"), mock.patch(
            f"{_SF}.get_workflow_state", return_value={"current_step": "feedback_completed", "score": 70}
        ), mock.patch(f"{_SF}._persist_attempt_and_lock_questions"), mock.patch(
            f"{_SF}._update_workflow_session_tokens"
        ):
            result = submit_answers("t-sub-5", {"q1": "2"}, user_id=None)
        self.assertEqual(result["current_step"], "feedback_completed")
        flow.update_state.assert_called_once()  # 写入 user_answers


class GetWorkflowStateTests(UnitTestCase):
    """get_workflow_state / get_workflow_history"""

    def setUp(self):
        _clear_cache()
        self.addCleanup(_clear_cache)

    def test_memory_hit(self):
        """内存 state 命中 → 直接返回 values"""
        flow = mock.Mock()
        flow.get_state.return_value = mock.Mock(values={"current_step": "waiting_for_answers"})
        with mock.patch(f"{_SF}._get_cached_study_flow", return_value=flow):
            result = get_workflow_state("t-state-1")
        self.assertEqual(result["current_step"], "waiting_for_answers")

    def test_falls_back_to_persistence(self):
        """内存获取失败 → 从持久化服务恢复"""
        flow = mock.Mock()
        flow.get_state.side_effect = RuntimeError("no checkpoint")
        saved = {"current_step": "grading", "score": 50}
        with mock.patch(f"{_SF}._get_cached_study_flow", return_value=flow), mock.patch(
            f"{_SF}._get_study_flow", return_value=flow
        ), mock.patch(f"{_SF}.persistence_service") as persistence:
            persistence.load_workflow_state.return_value = saved
            result = get_workflow_state("t-state-2")
        self.assertEqual(result, saved)
        flow.graph.update_state.assert_called_once()

    def test_returns_none_when_no_state(self):
        """内存与持久化均无 → None"""
        flow = mock.Mock()
        flow.get_state.return_value = None
        with mock.patch(f"{_SF}._get_cached_study_flow", return_value=flow), mock.patch(
            f"{_SF}.persistence_service"
        ) as persistence:
            persistence.load_workflow_state.return_value = None
            self.assertIsNone(get_workflow_state("t-state-3"))

    def test_history_collects_state_metadata(self):
        """get_workflow_history 聚合 checkpoint 历史"""
        flow = mock.Mock()
        entry = mock.Mock(metadata={"step": 1}, values={"current_step": "planner"})
        flow.graph.get_state_history.return_value = iter([entry])
        with mock.patch(f"{_SF}._get_cached_study_flow", return_value=flow):
            history = get_workflow_history("t-hist-1")
        self.assertEqual(history[0]["step"], 1)
        self.assertEqual(history[0]["values"]["current_step"], "planner")

    def test_history_swallows_errors(self):
        """历史获取异常 → 返回空列表"""
        flow = mock.Mock()
        flow.graph.get_state_history.side_effect = RuntimeError("boom")
        with mock.patch(f"{_SF}._get_cached_study_flow", return_value=flow):
            self.assertEqual(get_workflow_history("t-hist-2"), [])


class PrepareRestartTests(TestCase):
    """prepare_restart：继续练习准备"""

    def setUp(self):
        _clear_cache()
        self.addCleanup(_clear_cache)
        self.user = User.objects.create_user(username="sf-restart", password="pw123456")

    def _make_old_session(self, **overrides) -> WorkflowSession:
        defaults = {
            "thread_id": f"study_{uuid.uuid4().hex[:8]}",
            "user_question": "什么是闭包",
            "learning_plan": {"topic": "闭包", "difficulty": "beginner"},
            "retry_count": 1,
            "created_by": self.user,
            "knowledge_base_ids": ["kb1"],
            "provider_id": "openai",
            "model_name": "gpt-4o-mini",
            "temperature": 0.3,
            "max_tokens": 1024,
            "special_params": {"thinking": True},
            "enable_deep_thinking": True,
            "use_web_search": True,
        }
        defaults.update(overrides)
        return WorkflowSession.objects.create(**defaults)

    def test_missing_session_raises(self):
        """旧 session 不存在 → ValueError"""
        with self.assertRaises(ValueError):
            prepare_restart("no-such-thread", user_id=self.user.id)

    def test_missing_plan_raises(self):
        """旧 session 缺 learning_plan → ValueError"""
        session = self._make_old_session(learning_plan=None)
        with self.assertRaises(ValueError):
            prepare_restart(session.thread_id, user_id=self.user.id)

    def test_success_creates_new_session(self):
        """成功准备：新 thread_id / 新会话（retry_count+1）/ 计划与配置继承"""
        old = self._make_old_session(root_thread_id=None)
        result = prepare_restart(old.thread_id, user_id=self.user.id)

        self.assertTrue(result["new_thread_id"].startswith("study_"))
        self.assertNotEqual(result["new_thread_id"], old.thread_id)
        self.assertEqual(result["new_retry_count"], 2)
        self.assertEqual(result["old_session"].thread_id, old.thread_id)

        state = result["initial_state"]
        self.assertEqual(state["learning_plan"]["topic"], "闭包")
        self.assertEqual(state["retry_count"], 2)
        self.assertEqual(state["root_thread_id"], old.thread_id)  # 根线程回填旧 thread
        self.assertEqual(state["provider_id"], "openai")
        self.assertTrue(state["enable_deep_thinking"])
        self.assertTrue(state["use_web_search"])

        new_session = WorkflowSession.objects.get(thread_id=result["new_thread_id"])
        self.assertEqual(new_session.retry_count, 2)
        self.assertEqual(new_session.root_thread_id, old.thread_id)
        self.assertEqual(new_session.created_by_id, self.user.id)

    def test_root_thread_id_inherited(self):
        """已有 root_thread_id → 新会话继续继承原根线程"""
        old = self._make_old_session(root_thread_id="root-origin")
        result = prepare_restart(old.thread_id, user_id=self.user.id)
        new_session = WorkflowSession.objects.get(thread_id=result["new_thread_id"])
        self.assertEqual(new_session.root_thread_id, "root-origin")
        self.assertEqual(result["initial_state"]["root_thread_id"], "root-origin")


class PersistAttemptTests(TestCase):
    """_persist_attempt_and_lock_questions：轮次记录与题目锁定"""

    def setUp(self):
        self.user = User.objects.create_user(username="sf-attempt", password="pw123456")
        self.session = WorkflowSession.objects.create(
            thread_id="t-att", user_question="Q", created_by=self.user
        )

    def _make_question(self, q_id: str, status) -> WorkflowQuestion:
        return WorkflowQuestion.objects.create(
            session=self.session,
            question_id=q_id,
            attempt_index=0,
            question_index=1,
            type="multiple_choice",
            question="Q",
            correct_answer="2",
            points=10,
            status=status,
        )

    def test_creates_attempt_and_locks_scored(self):
        """创建 WorkflowAttempt，且该轮 SCORED 题目 → LOCKED，PENDING 不受影响"""
        scored = self._make_question("q1_r0", WorkflowQuestionStatus.SCORED)
        pending = self._make_question("q2_r0", WorkflowQuestionStatus.PENDING)
        _persist_attempt_and_lock_questions("t-att", {"retry_count": 0, "score": 85, "feedback": "good"})

        attempt = WorkflowAttempt.objects.get(session=self.session, attempt_index=0)
        self.assertEqual(attempt.total_score, 85)
        self.assertEqual(attempt.thread_id, "t-att")

        scored.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(scored.status, WorkflowQuestionStatus.LOCKED)
        self.assertEqual(pending.status, WorkflowQuestionStatus.PENDING)

    def test_attempt_update_idempotent(self):
        """同轮次重复落库 → 更新而非新增"""
        self._make_question("q1_r0", WorkflowQuestionStatus.SCORED)
        _persist_attempt_and_lock_questions("t-att", {"retry_count": 0, "score": 60, "feedback": "a"})
        _persist_attempt_and_lock_questions("t-att", {"retry_count": 0, "score": 70, "feedback": "b"})
        self.assertEqual(WorkflowAttempt.objects.filter(session=self.session, attempt_index=0).count(), 1)
        self.assertEqual(WorkflowAttempt.objects.get(session=self.session, attempt_index=0).total_score, 70)

    def test_missing_session_no_crash(self):
        """thread_id 无对应会话 → 跳过（不抛异常）"""
        _persist_attempt_and_lock_questions("no-such", {"retry_count": 0, "score": 1, "feedback": ""})
        self.assertTrue(True)


class UpdateWorkflowSessionTokensTests(TestCase):
    """_update_workflow_session_tokens：token/耗时统计"""

    def setUp(self):
        self.user = User.objects.create_user(username="sf-tokens", password="pw123456")
        self.session = WorkflowSession.objects.create(
            thread_id="t-token", user_question="Q", created_by=self.user
        )

    def test_absolute_mode_overwrites(self):
        """非增量模式：覆盖 token_count 与 response_time"""
        _update_workflow_session_tokens("t-token", 100, 1.5)
        self.session.refresh_from_db()
        self.assertEqual(self.session.token_count, 100)
        self.assertEqual(self.session.response_time, 1.5)

    def test_incremental_mode_accumulates(self):
        """增量模式：累加 token_count 与 response_time"""
        _update_workflow_session_tokens("t-token", 100, 1.0)
        _update_workflow_session_tokens("t-token", 50, 0.5, is_incremental=True)
        self.session.refresh_from_db()
        self.assertEqual(self.session.token_count, 150)
        self.assertEqual(self.session.response_time, 1.5)

    def test_missing_session_no_crash(self):
        """会话不存在 → 跳过（不抛异常）"""
        _update_workflow_session_tokens("no-such", 10, 0.1)
        self.assertTrue(True)


class WorkflowServiceTests(TestCase):
    """WorkflowService：结果映射 / 删除 / 列表"""

    def setUp(self):
        _clear_cache()
        self.addCleanup(_clear_cache)
        self.user = User.objects.create_user(username="sf-service", password="pw123456")

    def test_start_workflow_maps_result(self):
        """start_workflow 结果映射（status/message/thread_id）"""
        flow_result = {
            "current_step": "waiting_for_answers",
            "learning_plan": {"topic": "闭包"},
            "quiz": _make_quiz(),
            "retry_count": 0,
        }
        with mock.patch(f"{_WS}.start_study_flow", return_value=flow_result) as start:
            result = WorkflowService.start_workflow("什么是闭包", thread_id="t-svc-1", user_id=self.user.id)
        self.assertEqual(result["thread_id"], "t-svc-1")
        self.assertEqual(result["status"], "waiting_for_answers")
        self.assertEqual(result["message"], "学习计划和练习题已生成")
        self.assertEqual(result["quiz"]["total_points"], 10)
        start.assert_called_once()

    def test_start_workflow_generates_thread_id(self):
        """未传 thread_id → 自动生成 study_ 前缀"""
        with mock.patch(f"{_WS}.start_study_flow", return_value={"current_step": "waiting_for_answers"}):
            result = WorkflowService.start_workflow("Q")
        self.assertTrue(result["thread_id"].startswith("study_"))

    def test_submit_maps_retry_status(self):
        """should_retry=True → status=retry"""
        with mock.patch(f"{_WS}.submit_answers", return_value={"should_retry": True, "retry_count": 1}):
            result = WorkflowService.submit_user_answers("t-svc-2", {"q1": "x"})
        self.assertEqual(result["status"], "retry")
        self.assertIn("重新生成练习题", result["message"])

    def test_submit_maps_completed_status(self):
        """score>=60 且无重试 → completed"""
        with mock.patch(f"{_WS}.submit_answers", return_value={"should_retry": False, "score": 80}):
            result = WorkflowService.submit_user_answers("t-svc-3", {"q1": "x"})
        self.assertEqual(result["status"], "completed")
        self.assertIn("恭喜", result["message"])

    def test_submit_maps_failed_status(self):
        """score<60 且无重试 → failed"""
        with mock.patch(f"{_WS}.submit_answers", return_value={"should_retry": False, "score": 40}):
            result = WorkflowService.submit_user_answers("t-svc-4", {"q1": "x"})
        self.assertEqual(result["status"], "failed")

    def test_delete_workflow_soft_deletes(self):
        """删除工作流：软删除 session + 清理 checkpoint/文件（mock）"""
        session = WorkflowSession.objects.create(
            thread_id="t-svc-del", user_question="Q", created_by=self.user
        )

        async def _fake_delete(thread_id):
            return True

        with mock.patch(
            "Django_xm.apps.ai_engine.services.checkpointer_factory.delete_thread_checkpoints",
            side_effect=_fake_delete,
        ), mock.patch(
            "Django_xm.apps.core.services.file_manager.get_file_manager"
        ) as fm_factory:
            result = WorkflowService.delete_workflow("t-svc-del", user_id=self.user.id)
        self.assertEqual(result["status"], "deleted")
        session.refresh_from_db()
        self.assertTrue(session.is_deleted)
        fm_factory.return_value.delete_task_files.assert_called_once_with("t-svc-del", "workflow")

    def test_delete_workflow_without_session_raises(self):
        """无权限/不存在 → ValueError（user_id 过滤后无记录）"""
        other = User.objects.create_user(username="sf-service-other", password="pw123456")
        WorkflowSession.objects.create(thread_id="t-svc-del2", user_question="Q", created_by=self.user)
        with self.assertRaises(ValueError):
            WorkflowService.delete_workflow("t-svc-del2", user_id=other.id)

    def test_list_user_workflows(self):
        """列表委托持久化服务：仅本人未删除会话"""
        WorkflowSession.objects.create(thread_id="t-list-1", user_question="Q1", created_by=self.user)
        deleted = WorkflowSession.objects.create(thread_id="t-list-2", user_question="Q2", created_by=self.user)
        deleted.soft_delete()
        result = WorkflowService.list_user_workflows(self.user.id)
        self.assertEqual(result.count(), 1)
        self.assertEqual(result.first().thread_id, "t-list-1")

    def test_get_status_and_history_delegation(self):
        """get_workflow_status / get_workflow_history 委托 study_flow"""
        with mock.patch(f"{_WS}.get_workflow_state", return_value={"current_step": "grading"}) as state:
            self.assertEqual(WorkflowService.get_workflow_status("t-svc-9"), {"current_step": "grading"})
        state.assert_called_once_with("t-svc-9")
        with mock.patch(f"{_WS}.get_workflow_history", return_value=[{"step": 1}]) as history:
            self.assertEqual(WorkflowService.get_workflow_history("t-svc-9"), [{"step": 1}])
        history.assert_called_once_with("t-svc-9")


class CrossAppTests(TestCase):
    """cross_app.get_user_workflow_count：跨 app 计数"""

    def test_counts_undeleted_sessions(self):
        """软删除会话不计数；无会话用户计数为 0"""
        from Django_xm.apps.learning.services.cross_app import get_user_workflow_count

        user = User.objects.create_user(username="sf-cross", password="pw123456")
        other = User.objects.create_user(username="sf-cross-other", password="pw123456")
        WorkflowSession.objects.create(thread_id="t-cross-1", user_question="Q", created_by=user)
        WorkflowSession.objects.create(thread_id="t-cross-2", user_question="Q", created_by=user)
        deleted = WorkflowSession.objects.create(thread_id="t-cross-3", user_question="Q", created_by=user)
        deleted.soft_delete()
        self.assertEqual(get_user_workflow_count(user), 2)
        self.assertEqual(get_user_workflow_count(other), 0)
