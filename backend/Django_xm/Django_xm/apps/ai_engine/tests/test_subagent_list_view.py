"""SubAgentListView recursive 全树查询测试（spec Task 2：消除 429 请求放大）。

覆盖：
- 三层嵌套（root→B1/B2→C1）：recursive=true 一次请求返回 B1/B2/C1 全树，
  每条含 parent_thread_id（前端据此构建嵌套关系）
- recursive 模式下树中任一实例 metadata.user_id 不匹配 → 权限拒绝（403）
- 无 recursive 参数 → 只返回直接子代理（历史行为不变）
- 环防护（BFS 去重）：父子环数据不死循环、不重复返回

隔离策略：settings.test（内存缓存/测试库）+ APIClient.force_authenticate，
Django TestCase 事务回滚；不依赖真实 Redis/LLM。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    conda run -n langchain_xm python -m unittest Django_xm.apps.ai_engine.tests.test_subagent_list_view -v
"""

import os
from datetime import UTC, datetime, timedelta

# python -m unittest 直跑时：Django_xm/__init__.py → celery.py 已将
# DJANGO_SETTINGS_MODULE setdefault 为 dev settings，此处必须强制覆盖为 test
os.environ["DJANGO_SETTINGS_MODULE"] = "Django_xm.settings.test"
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from Django_xm.apps.ai_engine.models import SubAgentInstance

SUBAGENTS_URL = "/api/v1/ai-engine/subagents/"
BASE_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _make_instance(thread_id: str, parent_thread_id: str, *, user_id=None, offset_seconds: int = 0) -> SubAgentInstance:
    """构造一条子代理实例（created_at 偏移保证排序确定）。"""
    SubAgentInstance.objects.create(
        thread_id=thread_id,
        parent_thread_id=parent_thread_id,
        metadata={"user_id": user_id} if user_id is not None else {},
    )
    # auto_now_add 不可注入，创建后显式更新 created_at
    SubAgentInstance.objects.filter(thread_id=thread_id).update(
        created_at=BASE_TIME + timedelta(seconds=offset_seconds)
    )
    return SubAgentInstance.objects.get(thread_id=thread_id)


class SubAgentListViewRecursiveTests(TestCase):
    """Task 2：服务端单请求 BFS 全树查询。"""

    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(username="owner", password="pw123456")
        self.client.force_authenticate(user=self.user)

    def _build_three_layer_tree(self, *, user_id=None):
        """构造三层嵌套：root（非实例）→ B1/B2 → C1（C1 挂在 B1 下）。"""
        _make_instance("sub-b1", "session-root", user_id=user_id, offset_seconds=10)
        _make_instance("sub-b2", "session-root", user_id=user_id, offset_seconds=20)
        _make_instance("sub-c1", "sub-b1", user_id=user_id, offset_seconds=30)

    def test_recursive_returns_full_tree(self):
        """① recursive=true：三层嵌套一次请求返回 B1/B2/C1 全部，含 parent_thread_id。"""
        self._build_three_layer_tree(user_id=self.user.id)

        resp = self.client.get(
            f"{SUBAGENTS_URL}?parent_thread_id=session-root&recursive=true"
        )

        self.assertEqual(resp.status_code, 200, resp.content)
        subagents = resp.json()["data"]["subagents"]
        self.assertEqual(
            sorted(item["thread_id"] for item in subagents), ["sub-b1", "sub-b2", "sub-c1"]
        )
        by_thread = {item["thread_id"]: item for item in subagents}
        self.assertEqual(by_thread["sub-b1"]["parent_thread_id"], "session-root")
        self.assertEqual(by_thread["sub-b2"]["parent_thread_id"], "session-root")
        self.assertEqual(by_thread["sub-c1"]["parent_thread_id"], "sub-b1")

    def test_recursive_result_sorted_by_created_at(self):
        """② recursive=true：全树结果按 created_at 升序（B1→B2→C1）。"""
        self._build_three_layer_tree(user_id=self.user.id)

        resp = self.client.get(
            f"{SUBAGENTS_URL}?parent_thread_id=session-root&recursive=true"
        )

        self.assertEqual(resp.status_code, 200, resp.content)
        subagents = resp.json()["data"]["subagents"]
        self.assertEqual([item["thread_id"] for item in subagents], ["sub-b1", "sub-b2", "sub-c1"])

    def test_recursive_denies_when_any_tree_instance_owned_by_other(self):
        """③ recursive 全树权限校验：深层实例（C1）user_id 不匹配 → 403。"""
        other = get_user_model().objects.create_user(username="intruder", password="pw123456")
        self._build_three_layer_tree(user_id=self.user.id)
        # 直接子代理归属当前用户，孙级 C1 归属他人
        SubAgentInstance.objects.filter(thread_id="sub-c1").update(metadata={"user_id": other.id})

        resp = self.client.get(
            f"{SUBAGENTS_URL}?parent_thread_id=session-root&recursive=true"
        )

        self.assertEqual(resp.status_code, 403, resp.content)

    def test_without_recursive_returns_direct_children_only(self):
        """④ 无 recursive 参数：只返回直接子代理（B1/B2），行为不变。"""
        self._build_three_layer_tree(user_id=self.user.id)

        resp = self.client.get(f"{SUBAGENTS_URL}?parent_thread_id=session-root")

        self.assertEqual(resp.status_code, 200, resp.content)
        subagents = resp.json()["data"]["subagents"]
        self.assertEqual(
            sorted(item["thread_id"] for item in subagents), ["sub-b1", "sub-b2"]
        )

    def test_without_recursive_ignores_deep_instance_ownership(self):
        """⑤ 非 recursive：孙级归属他人不影响直接子代理查询（校验只覆盖返回集）。"""
        other = get_user_model().objects.create_user(username="intruder2", password="pw123456")
        self._build_three_layer_tree(user_id=self.user.id)
        SubAgentInstance.objects.filter(thread_id="sub-c1").update(metadata={"user_id": other.id})

        resp = self.client.get(f"{SUBAGENTS_URL}?parent_thread_id=session-root")

        self.assertEqual(resp.status_code, 200, resp.content)

    def test_recursive_cycle_does_not_loop_forever(self):
        """⑥ 环防护：自环脏数据（thread_id == parent_thread_id）时 BFS 去重不死循环、不重复。

        单 parent 字段模型下每个实例父节点唯一，普通父子环（环成员的父均在环内）
        从根不可达；实际可达的环形态是自环——查询根自身即脏数据父，无去重时
        filter(parent__in={X}) 每层都返回 X 自身，BFS 永不终止。
        """
        _make_instance("sub-x", "sub-x", user_id=self.user.id, offset_seconds=10)

        resp = self.client.get(f"{SUBAGENTS_URL}?parent_thread_id=sub-x&recursive=true")

        self.assertEqual(resp.status_code, 200, resp.content)
        subagents = resp.json()["data"]["subagents"]
        self.assertEqual([item["thread_id"] for item in subagents], ["sub-x"])

    def test_recursive_on_leaf_returns_empty(self):
        """⑦ recursive=true 但无任何子代理：返回空列表。"""
        resp = self.client.get(
            f"{SUBAGENTS_URL}?parent_thread_id=no-such-root&recursive=true"
        )

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["data"]["subagents"], [])
