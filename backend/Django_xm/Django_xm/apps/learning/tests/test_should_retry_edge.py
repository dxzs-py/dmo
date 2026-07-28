"""should_continue 条件边单元测试

验证 study_flow.should_continue 条件边函数的分支逻辑。

当前实现（study_flow.should_continue）:
    should_retry=True 且 retry_count<3 -> "retry"（重新出题）
    其他情况                          -> "end"（结束流程）

注：原 should_retry_edge 函数已重命名为 should_continue，
    语义改为按 should_retry + retry_count 决定是否重试。

运行方式：
    conda activate langchain_xm
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    python manage.py test Django_xm.apps.learning.tests.test_should_retry_edge -v 2
"""

from django.test import SimpleTestCase

from Django_xm.apps.learning.services.study_flow import should_continue


class ShouldContinueTestCase(SimpleTestCase):
    """should_continue 条件边函数测试"""

    def test_should_retry_false_returns_end(self):
        """should_retry=False 时返回 'end'"""
        state = {"should_retry": False, "retry_count": 0}
        self.assertEqual(should_continue(state), "end")

    def test_should_retry_true_low_retry_returns_retry(self):
        """should_retry=True, retry_count=0 -> 'retry'（未达上限）"""
        state = {"should_retry": True, "retry_count": 0}
        self.assertEqual(should_continue(state), "retry")

    def test_should_retry_true_below_max_returns_retry(self):
        """should_retry=True, retry_count=2 -> 'retry'（仍在上限内）"""
        state = {"should_retry": True, "retry_count": 2}
        self.assertEqual(should_continue(state), "retry")

    def test_should_retry_true_at_max_returns_end(self):
        """should_retry=True, retry_count=3 -> 'end'（达上限）"""
        state = {"should_retry": True, "retry_count": 3}
        self.assertEqual(should_continue(state), "end")

    def test_should_retry_true_exceed_max_returns_end(self):
        """should_retry=True, retry_count=5 -> 'end'（超上限）"""
        state = {"should_retry": True, "retry_count": 5}
        self.assertEqual(should_continue(state), "end")

    def test_missing_should_retry_defaults_false_returns_end(self):
        """缺失 should_retry key 时默认 False -> 'end'"""
        state = {"retry_count": 0}
        self.assertEqual(should_continue(state), "end")

    def test_missing_retry_count_defaults_zero_returns_retry(self):
        """should_retry=True 且缺失 retry_count key 时默认 0 -> 'retry'"""
        state = {"should_retry": True}
        self.assertEqual(should_continue(state), "retry")

    def test_should_retry_falsy_value_returns_end(self):
        """should_retry 为 falsy 值（如 None/0/""）时返回 'end'"""
        for falsy in (None, 0, "", False):
            with self.subTest(should_retry=falsy):
                state = {"should_retry": falsy, "retry_count": 0}
                self.assertEqual(should_continue(state), "end")
