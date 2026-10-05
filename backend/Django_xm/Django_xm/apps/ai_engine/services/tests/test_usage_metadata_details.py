"""D3（AI-5）usage 明细计量回归测试：reasoning/cached 分层明细不再恒 0。

契约（remediate-rag-ai-correctness D3）：
1. 标准 usage_metadata 结构（langchain-core）：明细位于
   input_token_details（cache_read/cache_creation/reasoning）与
   output_token_details（reasoning）子字典；
2. TokenDetailTracker / UsageTracker.update_from_metadata 从标准位置读取
   并回填四维（input/output/reasoning/cached）——此前读取顶层
   reasoning_tokens/cached_tokens 键（标准结构中不存在），恒 0；
3. 缺 details 子字典的旧消息不炸、不误计。

运行方式：
   python manage.py test Django_xm.apps.ai_engine.services.tests.test_usage_metadata_details --noinput
"""

import unittest

from Django_xm.apps.ai_engine.services.cost_tracker import TokenDetailTracker
from Django_xm.apps.ai_engine.services.usage_tracker import UsageTracker


def _std_usage_metadata(**overrides):
    """标准 langchain-core usage_metadata（OpenAI o 系 + 缓存形态）。"""
    meta = {
        "input_tokens": 100,
        "output_tokens": 60,
        "total_tokens": 160,
        "input_token_details": {"cache_read": 40, "cache_creation": 10},
        "output_token_details": {"reasoning": 30},
    }
    meta.update(overrides)
    return {"usage_metadata": meta}


class TokenDetailTrackerTests(unittest.TestCase):
    def test_standard_metadata_fills_all_dimensions(self):
        tracker = TokenDetailTracker(default_model="m1")
        tracker.start_record("m1")
        tracker.update_from_metadata(_std_usage_metadata())
        record = tracker.finish_record()

        self.assertEqual(record.input_tokens, 100)
        self.assertEqual(record.output_tokens, 60)
        self.assertEqual(record.reasoning_tokens, 30)
        self.assertEqual(record.cached_input_tokens, 40)
        self.assertEqual(record.cache_creation_tokens, 10)

    def test_missing_details_leaves_zero(self):
        """旧消息无 details 子字典 → 不炸、明细为 0（总量正常累加）。"""
        tracker = TokenDetailTracker(default_model="m1")
        tracker.start_record("m1")
        tracker.update_from_metadata(
            {"usage_metadata": {"input_tokens": 50, "output_tokens": 20, "total_tokens": 70}}
        )
        record = tracker.finish_record()
        self.assertEqual(record.input_tokens, 50)
        self.assertEqual(record.output_tokens, 20)
        self.assertEqual(record.reasoning_tokens, 0)
        self.assertEqual(record.cached_input_tokens, 0)
        self.assertEqual(record.cache_creation_tokens, 0)

    def test_accumulate_across_calls(self):
        tracker = TokenDetailTracker(default_model="m1")
        tracker.start_record("m1")
        tracker.update_from_metadata(_std_usage_metadata())
        tracker.update_from_metadata(_std_usage_metadata())
        record = tracker.finish_record()
        self.assertEqual(record.reasoning_tokens, 60)
        self.assertEqual(record.cached_input_tokens, 80)


class UsageTrackerTests(unittest.TestCase):
    def test_standard_metadata_fills_details(self):
        tracker = UsageTracker(model_id="m1")
        tracker.update_from_metadata(_std_usage_metadata())
        self.assertEqual(tracker.usage.input_tokens, 100)
        self.assertEqual(tracker.usage.output_tokens, 60)
        self.assertEqual(tracker.usage.reasoning_tokens, 30)
        self.assertEqual(tracker.usage.cached_input_tokens, 40)

    def test_missing_details_leaves_zero(self):
        tracker = UsageTracker(model_id="m1")
        tracker.update_from_metadata(
            {"usage_metadata": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}
        )
        self.assertEqual(tracker.usage.reasoning_tokens, 0)
        self.assertEqual(tracker.usage.cached_input_tokens, 0)
