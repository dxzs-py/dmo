"""learning 视图层与序列化器测试（ARCH-21，DRF APIClient）。

覆盖：
- 认证：匿名访问任务列表 401
- WorkflowListView：本人会话分页列表、status/search 过滤、他人会话不可见
- WorkflowStatusView：未知会话 404、状态查询 200
- WorkflowQuestionListView：题目列表、PENDING 题目隐藏答案（防作弊策略）、
  attempt_index 过滤、无会话 404
- WorkflowQuestionUpdateView：改答案重判（MC 字母匹配）、attempt 总分重算、缺参 400
- WorkflowAttemptListView：轮次历史列表
- WorkflowTaskDeleteView：软删除 + checkpoint/文件清理（mock）
- WorkflowSubmitView：缺 answers 400、未知会话 404
- 序列化器：WorkflowStartSerializer 双字段校验、WorkflowSubmitSerializer、
  WorkflowQuestionSerializer 答案字段安全策略

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.learning.tests.test_learning_views --noinput
"""

import os
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from Django_xm.apps.core.throttling import LearningRateThrottle
from Django_xm.apps.learning.models import (
    WorkflowAttempt,
    WorkflowQuestion,
    WorkflowQuestionStatus,
    WorkflowSession,
)

User = get_user_model()

LEARNING_URL = "/api/v1/learning/"

# test.py 的 DEFAULT_THROTTLE_RATES 未包含 learning scope，节流视图
# （submit/question_update/start）在 allow_request 阶段 ImproperlyConfigured → 500。
# DRF SimpleRateThrottle.THROTTLE_RATES 在类定义时绑定，override_settings 无法刷新，
# 此处直接 patch 类属性补齐 learning scope（仅测试进程内生效，不改生产配置）。
_THROTTLE_RATES = {
    **LearningRateThrottle.THROTTLE_RATES,
    "learning": "9999/min",
}


def _patch_learning_throttle(testcase) -> None:
    """为节流视图测试补齐 learning 限流 scope（自动清理）"""
    patcher = mock.patch.object(LearningRateThrottle, "THROTTLE_RATES", _THROTTLE_RATES)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _make_session(user, thread_id: str, **overrides) -> WorkflowSession:
    """构造工作流会话"""
    defaults = {"thread_id": thread_id, "user_question": f"问题-{thread_id}", "created_by": user}
    defaults.update(overrides)
    return WorkflowSession.objects.create(**defaults)


def _make_question(session, q_id: str, status, q_type="multiple_choice", points=10) -> WorkflowQuestion:
    """构造工作流题目"""
    return WorkflowQuestion.objects.create(
        session=session,
        question_id=q_id,
        attempt_index=0,
        question_index=1,
        type=q_type,
        question="1+1=?",
        options=["1", "2", "3", "4"],
        correct_answer="2",
        explanation="加法",
        points=points,
        status=status,
    )


class AuthRequiredTests(TestCase):
    """认证守卫"""

    def setUp(self):
        self.client = APIClient()

    def test_anonymous_get_tasks_unauthorized(self):
        """匿名访问任务列表 → 401"""
        resp = self.client.get(f"{LEARNING_URL}tasks/")
        self.assertEqual(resp.status_code, 401, resp.content)


class WorkflowListViewTests(TestCase):
    """WorkflowListView：任务列表与过滤"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="lv-list", password="pw123456")
        self.other = User.objects.create_user(username="lv-list-other", password="pw123456")
        self.client.force_authenticate(user=self.user)
        _make_session(self.user, "lv-1", status="completed")
        _make_session(self.user, "lv-2", status="running", user_question="什么是装饰器")
        _make_session(self.other, "lv-3")
        deleted = _make_session(self.user, "lv-4")
        deleted.soft_delete()

    def test_lists_own_undeleted_sessions(self):
        """仅返回本人未删除会话"""
        resp = self.client.get(f"{LEARNING_URL}tasks/")
        self.assertEqual(resp.status_code, 200)
        data = resp.data["data"]
        thread_ids = {item["thread_id"] for item in data["items"]}
        self.assertEqual(thread_ids, {"lv-1", "lv-2"})
        self.assertEqual(data["total"], 2)

    def test_status_filter(self):
        """status 查询参数过滤"""
        resp = self.client.get(f"{LEARNING_URL}tasks/", {"status": "completed"})
        items = resp.data["data"]["items"]
        self.assertEqual([i["thread_id"] for i in items], ["lv-1"])

    def test_search_filter(self):
        """search 查询参数模糊匹配问题"""
        resp = self.client.get(f"{LEARNING_URL}tasks/", {"search": "装饰器"})
        items = resp.data["data"]["items"]
        self.assertEqual([i["thread_id"] for i in items], ["lv-2"])


class WorkflowStatusViewTests(TestCase):
    """WorkflowStatusView：状态查询"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="lv-status", password="pw123456")
        self.client.force_authenticate(user=self.user)

    def test_unknown_thread_not_found(self):
        """未知 thread_id → 404"""
        resp = self.client.get(f"{LEARNING_URL}status/no-such/")
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_status_state_none_not_found(self):
        """get_workflow_state 返回 None（内存与持久化均无状态）→ 404"""
        _make_session(self.user, "lv-st-1")
        with mock.patch(
            "Django_xm.apps.learning.views.WorkflowService.get_workflow_status", return_value=None
        ):
            resp = self.client.get(f"{LEARNING_URL}status/lv-st-1/")
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_status_returns_state_fields(self):
        """get_workflow_state 有结果 → 返回状态字段"""
        _make_session(self.user, "lv-st-2")
        state = {
            "current_step": "waiting_for_answers",
            "user_question": "Q",
            "score": 80,
            "learning_plan": {"topic": "闭包"},
        }
        with mock.patch("Django_xm.apps.learning.views.WorkflowService.get_workflow_status", return_value=state):
            resp = self.client.get(f"{LEARNING_URL}status/lv-st-2/")
        self.assertEqual(resp.status_code, 200)
        data = resp.data["data"]
        self.assertEqual(data["thread_id"], "lv-st-2")
        self.assertEqual(data["current_step"], "waiting_for_answers")
        self.assertEqual(data["score"], 80)

    def test_other_user_session_not_found(self):
        """他人会话 → 404（用户隔离）"""
        other = User.objects.create_user(username="lv-status-other", password="pw123456")
        _make_session(other, "lv-st-3")
        state = {"current_step": "waiting_for_answers"}
        with mock.patch("Django_xm.apps.learning.views.WorkflowService.get_workflow_status", return_value=state):
            resp = self.client.get(f"{LEARNING_URL}status/lv-st-3/")
        self.assertEqual(resp.status_code, 404, resp.content)


class WorkflowQuestionListViewTests(TestCase):
    """WorkflowQuestionListView：题目列表与答案隐藏策略"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="lv-questions", password="pw123456")
        self.client.force_authenticate(user=self.user)
        self.session = _make_session(self.user, "lv-q-1")

    def test_unknown_session_not_found(self):
        """无会话 → 404"""
        resp = self.client.get(f"{LEARNING_URL}no-such/questions/")
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_pending_question_hides_answer(self):
        """PENDING 题目不返回 correct_answer/explanation/is_correct/points_earned"""
        _make_question(self.session, "q1_r0", WorkflowQuestionStatus.PENDING)
        resp = self.client.get(f"{LEARNING_URL}lv-q-1/questions/")
        self.assertEqual(resp.status_code, 200)
        items = resp.data["data"]
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertNotIn("correct_answer", item)
        self.assertNotIn("explanation", item)
        self.assertNotIn("is_correct", item)
        self.assertNotIn("points_earned", item)
        # 题目内容与分值可见
        self.assertEqual(item["question"], "1+1=?")
        self.assertEqual(item["points"], 10)

    def test_scored_question_exposes_answer(self):
        """SCORED 题目返回答案与解析（结果展示/练习历史）"""
        _make_question(self.session, "q1_r0", WorkflowQuestionStatus.SCORED)
        resp = self.client.get(f"{LEARNING_URL}lv-q-1/questions/")
        item = resp.data["data"][0]
        self.assertEqual(item["correct_answer"], "2")
        self.assertEqual(item["explanation"], "加法")

    def test_attempt_index_filter(self):
        """attempt_index 查询参数过滤轮次"""
        _make_question(self.session, "q1_r0", WorkflowQuestionStatus.SCORED)
        q2 = _make_question(self.session, "q1_r1", WorkflowQuestionStatus.SCORED)
        q2.attempt_index = 1
        q2.save()
        resp = self.client.get(f"{LEARNING_URL}lv-q-1/questions/", {"attempt_index": "1"})
        items = resp.data["data"]
        self.assertEqual([i["question_id"] for i in items], ["q1_r1"])

    def test_invalid_attempt_index_ignored(self):
        """非法 attempt_index（非数字）被忽略而非报错"""
        _make_question(self.session, "q1_r0", WorkflowQuestionStatus.PENDING)
        resp = self.client.get(f"{LEARNING_URL}lv-q-1/questions/", {"attempt_index": "abc"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["data"]), 1)


class WorkflowQuestionUpdateViewTests(TestCase):
    """WorkflowQuestionUpdateView：修改答案并重新评分（节流视图，补 learning scope）"""

    def setUp(self):
        _patch_learning_throttle(self)
        self.client = APIClient()
        self.user = User.objects.create_user(username="lv-update", password="pw123456")
        self.client.force_authenticate(user=self.user)
        self.session = _make_session(self.user, "lv-u-1")
        # 内存 graph state 同步路径打桩（避免创建真实 checkpointer）
        patcher = mock.patch("Django_xm.apps.learning.views._get_cached_study_flow", side_effect=RuntimeError("skip"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_unknown_session_not_found(self):
        """无会话 → 404"""
        resp = self.client.put(f"{LEARNING_URL}no-such/questions/q1_r0/", {"user_answer": "2"}, format="json")
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_unknown_question_not_found(self):
        """题目不存在 → 404"""
        _make_question(self.session, "q1_r0", WorkflowQuestionStatus.SCORED)
        resp = self.client.put(f"{LEARNING_URL}lv-u-1/questions/zz_r0/", {"user_answer": "2"}, format="json")
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_missing_user_answer_rejected(self):
        """缺 user_answer → 400"""
        _make_question(self.session, "q1_r0", WorkflowQuestionStatus.SCORED)
        resp = self.client.put(f"{LEARNING_URL}lv-u-1/questions/q1_r0/", {}, format="json")
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_regrade_updates_question_and_attempt(self):
        """改答案重判：MC 字母答案 → 更新题目/重算 attempt.total_score/同步 session"""
        question = _make_question(self.session, "q1_r0", WorkflowQuestionStatus.SCORED, points=10)
        attempt = WorkflowAttempt.objects.create(
            session=self.session, attempt_index=0, thread_id="lv-u-1", total_score=0
        )
        self.session.quiz = {"total_points": 10}
        self.session.save()

        resp = self.client.put(
            f"{LEARNING_URL}lv-u-1/questions/q1_r0/", {"user_answer": "B"}, format="json"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.data["data"]
        self.assertEqual(data["attempt_total_score"], 100)

        question.refresh_from_db()
        self.assertTrue(question.is_correct)
        self.assertEqual(question.points_earned, 10)
        self.assertEqual(question.status, WorkflowQuestionStatus.SCORED)
        attempt.refresh_from_db()
        self.assertEqual(attempt.total_score, 100)

        # session 同步更新（user_answers/score/score_details）
        self.session.refresh_from_db()
        self.assertEqual(self.session.user_answers, {"q1": "B"})
        self.assertEqual(self.session.score, 100)

    def test_regrade_wrong_answer_zero_points(self):
        """改错答案：is_correct=False，0 分"""
        question = _make_question(self.session, "q1_r0", WorkflowQuestionStatus.SCORED, points=10)
        resp = self.client.put(
            f"{LEARNING_URL}lv-u-1/questions/q1_r0/", {"user_answer": "3"}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        question.refresh_from_db()
        self.assertFalse(question.is_correct)
        self.assertEqual(question.points_earned, 0)


class WorkflowAttemptListViewTests(TestCase):
    """WorkflowAttemptListView：练习轮次历史"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="lv-attempts", password="pw123456")
        self.client.force_authenticate(user=self.user)

    def test_unknown_session_not_found(self):
        """无会话 → 404"""
        resp = self.client.get(f"{LEARNING_URL}no-such/attempts/")
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_lists_attempts(self):
        """返回会话全部轮次"""
        session = _make_session(self.user, "lv-a-1")
        WorkflowAttempt.objects.create(session=session, attempt_index=0, thread_id="lv-a-1", total_score=60)
        WorkflowAttempt.objects.create(session=session, attempt_index=1, thread_id="lv-a-1", total_score=90)
        resp = self.client.get(f"{LEARNING_URL}lv-a-1/attempts/")
        self.assertEqual(resp.status_code, 200)
        items = resp.data["data"]
        self.assertEqual([i["attempt_index"] for i in items], [0, 1])


class WorkflowTaskDeleteViewTests(TestCase):
    """WorkflowTaskDeleteView：删除任务"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="lv-delete", password="pw123456")
        self.client.force_authenticate(user=self.user)

    def test_delete_soft_deletes_session(self):
        """删除：200 + 软删除 + checkpoint/文件清理（mock）"""
        session = _make_session(self.user, "lv-d-1")

        async def _fake_delete(thread_id):
            return True

        with mock.patch(
            "Django_xm.apps.ai_engine.services.checkpointer_factory.delete_thread_checkpoints",
            side_effect=_fake_delete,
        ), mock.patch("Django_xm.apps.core.services.file_manager.get_file_manager") as fm_factory:
            resp = self.client.delete(f"{LEARNING_URL}tasks/lv-d-1/")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["data"]["status"], "deleted")
        session.refresh_from_db()
        self.assertTrue(session.is_deleted)
        fm_factory.return_value.delete_task_files.assert_called_once_with("lv-d-1", "workflow")


class WorkflowSubmitViewTests(TestCase):
    """WorkflowSubmitView：提交答案（节流视图，补 learning scope）"""

    def setUp(self):
        _patch_learning_throttle(self)
        self.client = APIClient()
        self.user = User.objects.create_user(username="lv-submit", password="pw123456")
        self.client.force_authenticate(user=self.user)

    def test_missing_answers_rejected(self):
        """缺 answers 字段 → 400"""
        resp = self.client.post(f"{LEARNING_URL}submit/", {"thread_id": "lv-s-1"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_unknown_thread_not_found(self):
        """未知会话且持久化无状态 → 404"""
        resp = self.client.post(
            f"{LEARNING_URL}submit/", {"thread_id": "lv-s-nope", "answers": {"q1": "2"}}, format="json"
        )
        self.assertEqual(resp.status_code, 404, resp.content)


class SerializerTests(TestCase):
    """序列化器校验与安全策略"""

    def test_start_serializer_accepts_user_question(self):
        """user_question 单独提供即可"""
        from Django_xm.apps.learning.serializers import WorkflowStartSerializer

        serializer = WorkflowStartSerializer(data={"user_question": "什么是闭包"})
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_start_serializer_accepts_query_alias(self):
        """query 别名等价 user_question"""
        from Django_xm.apps.learning.serializers import WorkflowStartSerializer

        serializer = WorkflowStartSerializer(data={"query": "什么是闭包"})
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_start_serializer_requires_question(self):
        """两者均缺 → 校验失败"""
        from Django_xm.apps.learning.serializers import WorkflowStartSerializer

        serializer = WorkflowStartSerializer(data={})
        self.assertFalse(serializer.is_valid())

    def test_submit_serializer_requires_both_fields(self):
        """thread_id 与 answers 均必填"""
        from Django_xm.apps.learning.serializers import WorkflowSubmitSerializer

        self.assertFalse(WorkflowSubmitSerializer(data={"thread_id": "t"}).is_valid())
        self.assertFalse(WorkflowSubmitSerializer(data={"answers": {}}).is_valid())
        self.assertTrue(WorkflowSubmitSerializer(data={"thread_id": "t", "answers": {"q1": "a"}}).is_valid())

    def test_question_serializer_security_policy(self):
        """WorkflowQuestionSerializer：PENDING 隐藏答案字段，SCORED 暴露（DB 往返验证）"""
        from Django_xm.apps.learning.serializers import WorkflowQuestionSerializer

        user = User.objects.create_user(username="lv-ser", password="pw123456")
        session = _make_session(user, "lv-ser-1")
        pending = _make_question(session, "q1_r0", WorkflowQuestionStatus.PENDING)
        scored = _make_question(session, "q2_r0", WorkflowQuestionStatus.SCORED)
        pending_data = WorkflowQuestionSerializer(pending).data
        scored_data = WorkflowQuestionSerializer(scored).data
        self.assertNotIn("correct_answer", pending_data)
        self.assertIn("correct_answer", scored_data)
