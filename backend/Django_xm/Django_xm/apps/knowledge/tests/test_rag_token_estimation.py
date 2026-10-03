"""RAG 侧 Token 估算收敛表征测试（phase5 5-1.6：自造启发式 → TokenEstimator 委托）。

锁定行为：
1. 委托关系：_estimate_token_count 委托 TokenEstimator.estimate_tokens
   （patch 权威实现后返回值一致，证明不再走本地公式）；
2. 空文本 → 0（与权威实现及旧公式一致）；
3. 非空文本 → 正值且单调（更长文本估算不减小）；
4. 合理范围：纯 CJK 与纯英文估算落在旧自造公式值的有界邻域内
   （旧公式 CJK 1/char、英文 ~1/4 char；权威实现 tiktoken 对 CJK 约 1~1.5/char，
   故上界放宽到 2.5 倍，行为变化已在 rag_retrieval._estimate_token_count docstring 注明）。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.knowledge.tests.test_rag_token_estimation \
        --noinput --settings=Django_xm.settings.test
（纯单元测试，无 DB / Redis / LLM 依赖）
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from Django_xm.apps.context_manager.services.compression import TokenEstimator
from Django_xm.apps.knowledge.services.rag_retrieval import _estimate_token_count

_COMPRESSION = "Django_xm.apps.context_manager.services.compression"


class TokenEstimatorDelegationTests(unittest.TestCase):
    """收敛表征：RAG 侧不再自造公式，与上下文管理侧同源。"""

    def test_delegates_to_token_estimator(self):
        """patch 权威实现 → 本地函数返回其值（委托关系锁定）。"""
        with mock.patch.object(TokenEstimator, "estimate_tokens", return_value=4242):
            self.assertEqual(_estimate_token_count("任意文本"), 4242)

    def test_empty_text_returns_zero(self):
        self.assertEqual(_estimate_token_count(""), 0)

    def test_nonempty_text_positive_and_monotonic(self):
        short = "这是一个用于测试的中文短句。"
        long = short * 5
        est_short = _estimate_token_count(short)
        est_long = _estimate_token_count(long)
        self.assertGreater(est_short, 0)
        self.assertGreaterEqual(est_long, est_short)


class TokenEstimateReasonableRangeTests(unittest.TestCase):
    """与旧自造公式值的有界邻域校验（锁定收敛后行为不跑飞）。"""

    _CJK_TEXT = "上下文压缩引擎负责在对话超过模型窗口时自动压缩历史消息" * 2  # 50 字
    _EN_TEXT = "The context compression engine estimates tokens for budget control. " * 2  # 142 字符

    def test_cjk_estimate_within_reasonable_range(self):
        """旧公式值 = 1/char；新值应落在 [0.5x, 2.5x] 区间（tiktoken CJK 约 1~1.5/char）。"""
        estimate = _estimate_token_count(self._CJK_TEXT)
        old_formula_value = len(self._CJK_TEXT)
        self.assertGreaterEqual(estimate, int(old_formula_value * 0.5))
        self.assertLessEqual(estimate, int(old_formula_value * 2.5))

    def test_english_estimate_within_reasonable_range(self):
        """旧公式值 ≈ 1/4 char；新值应落在 [0.5x, 2.5x] 区间。"""
        estimate = _estimate_token_count(self._EN_TEXT)
        old_formula_value = len(self._EN_TEXT) // 4
        self.assertGreater(0.5 * old_formula_value, 0)
        self.assertGreaterEqual(estimate, int(old_formula_value * 0.5))
        self.assertLessEqual(estimate, int(old_formula_value * 2.5))

    def test_same_estimate_as_authoritative_estimator(self):
        """同一文本与 TokenEstimator 直连结果逐位一致（单源验证）。"""
        for text in (self._CJK_TEXT, self._EN_TEXT, "mixed 混合 text 文本 123"):
            with self.subTest(text=text[:20]):
                self.assertEqual(_estimate_token_count(text), TokenEstimator.estimate_tokens(text))
