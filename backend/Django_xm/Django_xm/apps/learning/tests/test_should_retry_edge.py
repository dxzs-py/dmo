"""should_retry_edge None 值防御性处理单元测试

验证：
1. score=None 时不报 TypeError，返回 "end"（自动重试已禁用）
2. retry_count=None 时不报 TypeError，返回 "end"
3. score=50, retry_count=1 时返回 "end"（自动重试已禁用）
4. score=80, retry_count=1 时返回 "end"
5. score=50, retry_count=5 时返回 "end"

背景：
should_retry_edge 已禁用自动重试逻辑，始终返回 "end"。
用户可通过"修改答案"功能修改第一次的答案，或通过"继续练习"手动重新出题。
本测试文件验证函数在 None/缺失字段等边界场景下不抛异常，统一返回 "end"。

运行方式：
    conda activate langchain_xm
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    python manage.py test Django_xm.apps.learning.tests.test_should_retry_edge
"""

from django.test import SimpleTestCase

from Django_xm.apps.learning.services.study_flow import should_retry_edge


class ShouldRetryEdgeTestCase(SimpleTestCase):
    """should_retry_edge 条件边单元测试"""

    def test_score_none_returns_end(self):
        """score=None 时不报 TypeError，返回 'end'

        自动重试已禁用，无论 score 为何值（含 None）都返回 "end"。
        """
        state = {"score": None, "retry_count": 0}
        result = should_retry_edge(state)
        self.assertEqual(result, "end")

    def test_retry_count_none_no_error(self):
        """retry_count=None 时不报 TypeError，返回 'end'"""
        state = {"score": 50, "retry_count": None}
        result = should_retry_edge(state)
        self.assertEqual(result, "end")

    def test_both_none_returns_end(self):
        """score=None, retry_count=None 时返回 'end'

        自动重试已禁用，所有输入统一返回 "end"。
        """
        state = {"score": None, "retry_count": None}
        result = should_retry_edge(state)
        self.assertEqual(result, "end")

    def test_low_score_low_retry_returns_end(self):
        """score=50, retry_count=1 时返回 'end'（自动重试已禁用）"""
        state = {"score": 50, "retry_count": 1}
        result = should_retry_edge(state)
        self.assertEqual(result, "end")

    def test_high_score_returns_end(self):
        """score=80, retry_count=1 时返回 'end'"""
        state = {"score": 80, "retry_count": 1}
        result = should_retry_edge(state)
        self.assertEqual(result, "end")

    def test_low_score_high_retry_returns_end(self):
        """score=50, retry_count=5 时返回 'end'"""
        state = {"score": 50, "retry_count": 5}
        result = should_retry_edge(state)
        self.assertEqual(result, "end")

    def test_missing_score_key_returns_end(self):
        """score key 不存在时返回 'end'（自动重试已禁用）"""
        state = {"retry_count": 0}
        result = should_retry_edge(state)
        self.assertEqual(result, "end")

    def test_missing_retry_count_key_returns_end(self):
        """retry_count key 不存在时返回 'end'"""
        state = {"score": 50}
        result = should_retry_edge(state)
        self.assertEqual(result, "end")
