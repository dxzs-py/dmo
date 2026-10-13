"""降级留痕测试（E5-03 后继，Task1 语义更新）。

锁定两处"不可用时必须 warning 留痕、不得伪造成功"的降级点：
1. compression.evaluate_summary_quality：评估模型不可用 → passed=False
   （评估视为未通过，禁止伪造默认 0.7 通过分）+ warning 留痕；
   原 _get_embedding 静默降级点已随 Task1 死路径清理删除。
2. termination_judge.ContextTerminationJudge._embedding_similarity：
   embedding 故障 → 返回 None 且有 warning（降级语义不变）。

patch 点说明：两处消费点均经 runtime_providers 注入的 provider，
本文件把故障注入点设为抛异常的 provider，降级留痕契约不变。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.context_manager.tests.test_silent_degradation_logging --noinput
"""

import os
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import SimpleTestCase

_EVALUATION_PROVIDER = "Django_xm.apps.context_manager.services.runtime_providers._evaluation_model_provider"
_EMBEDDING_PROVIDER = "Django_xm.apps.context_manager.services.runtime_providers._embedding_provider"


class EvaluationUnavailableDegradationLogTests(SimpleTestCase):
    """evaluate_summary_quality：评估不可用 → passed=False 且有 warning（不伪造通过）。"""

    def test_missing_provider_returns_not_passed_and_logs_warning(self):
        from Django_xm.apps.context_manager.services.compression import (
            CompressionConfig,
            ContextCompressionEngine,
        )

        # A11 后 _format_messages 为实例方法（读 config.preserve_tool_results），
        # 需真实实例（__init__ 仅赋 config/抽取器，无 provider 依赖）
        engine = ContextCompressionEngine(CompressionConfig())
        with (
            mock.patch(_EVALUATION_PROVIDER, None),
            self.assertLogs("Django_xm.apps.context_manager.services.compression", level="WARNING") as captured,
        ):
            result = engine.evaluate_summary_quality(
                [{"role": "user", "content": "问题"}], "待评估摘要"
            )

        self.assertFalse(result.passed)
        self.assertEqual(result.overall, 0.0)
        self.assertTrue(any("评估" in line for line in captured.output))

    def test_failing_provider_returns_not_passed_and_logs_warning(self):
        from Django_xm.apps.context_manager.services.compression import (
            CompressionConfig,
            ContextCompressionEngine,
        )

        engine = ContextCompressionEngine(CompressionConfig())
        with (
            mock.patch(_EVALUATION_PROVIDER, mock.MagicMock(side_effect=RuntimeError("评估模型挂了"))),
            self.assertLogs("Django_xm.apps.context_manager.services.compression", level="WARNING") as captured,
        ):
            result = engine.evaluate_summary_quality(
                [{"role": "user", "content": "问题"}], "待评估摘要"
            )

        self.assertFalse(result.passed)
        self.assertTrue(any("评估" in line for line in captured.output))


class TerminationJudgeEmbeddingDegradationLogTests(SimpleTestCase):
    """termination_judge._embedding_similarity：embedding 故障 → 返回 None 且有 warning。"""

    def test_failure_returns_none_and_logs_warning(self):
        from Django_xm.apps.context_manager.services.termination_judge import ContextTerminationJudge

        with (
            mock.patch(_EMBEDDING_PROVIDER, mock.MagicMock(side_effect=RuntimeError("embedding 挂了"))),
            self.assertLogs("Django_xm.apps.context_manager.services.termination_judge", level="WARNING") as captured,
        ):
            result = ContextTerminationJudge._embedding_similarity("文本A", "文本B")

        self.assertIsNone(result)
        self.assertTrue(any("embedding" in line for line in captured.output))
