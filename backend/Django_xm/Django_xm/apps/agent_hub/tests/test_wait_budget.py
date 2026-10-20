"""wait_for_subagent fan-in 汇总预算与失败路径单元测试。

覆盖：
- 每条结果摘要压缩与单条硬截断（JSON 结构化 + 记录数标注进入汇总）；
- 总量预算收敛顺序（failed 优先保留，completed 均分压缩，无效碎片占位符，
  尾部截断标注）；
- 非法 resume 值：逐 id 报告真实状态（不再伪造 failed），查无记录报 unknown
  并附原始 resume 值；
- interrupt payload 携带 deadline 时间戳；
- 超时 resume（_subagent_wait_timeout）：走失败路径返回真实状态。

隔离策略：runtime / interrupt 全 mock，不触碰 DB/LangGraph。
"""

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

_WAIT_MODULE = "Django_xm.apps.agent_hub.subagent_tools.wait"
_RUNTIME_MODULE = "Django_xm.apps.ai_engine.subagent_runtime"


def _make_runtime(instances: dict):
    runtime = mock.Mock()
    runtime.get_instance = mock.AsyncMock(side_effect=instances.get)
    return runtime


class TestFormatBudget(unittest.TestCase):
    """format_subagent_results_text：单条压缩 + 总量预算收敛。"""

    def test_no_truncation_within_budget(self):
        from Django_xm.apps.agent_hub.subagent_tools.wait import format_subagent_results_text

        results = [
            {"subagent_thread_id": "s1", "status": "completed", "result": "结果一"},
            {"subagent_thread_id": "s2", "status": "failed", "result": "报错了"},
        ]
        out = format_subagent_results_text(results, result_max_chars=5000, total_budget_chars=48000)
        self.assertIn("子代理 s1 已完成。结果：\n结果一", out)
        self.assertIn("子代理 s2 执行失败：\n报错了", out)
        self.assertNotIn("已截断", out)

    def test_per_result_json_summary_with_record_count(self):
        import json

        from Django_xm.apps.agent_hub.subagent_tools.wait import format_subagent_results_text

        records = [{"title": "t" * 300, "rank": i} for i in range(7)]
        results = [
            {"subagent_thread_id": "s1", "status": "completed", "result": json.dumps(records, ensure_ascii=False)}
        ]
        out = format_subagent_results_text(results, result_max_chars=5000, total_budget_chars=48000)
        # JSON 被结构化压缩并自带记录数标注
        self.assertIn("共 7 条记录，字段: title, rank", out)
        self.assertIn("... 还有 2 条记录", out)

    def test_per_result_hard_truncation(self):
        from Django_xm.apps.agent_hub.subagent_tools.wait import format_subagent_results_text

        results = [{"subagent_thread_id": "s1", "status": "completed", "result": "z" * 8000}]
        out = format_subagent_results_text(results, result_max_chars=1000, total_budget_chars=48000)
        self.assertIn("z" * 1000 + "...", out)
        self.assertNotIn("z" * 1001, out)

    def test_total_budget_failed_kept_completed_compressed(self):
        """超总预算：failed 错误信息保留，completed 均分剩余预算压缩，尾部标注截断。"""
        from Django_xm.apps.agent_hub.subagent_tools.wait import format_subagent_results_text

        results = [
            {"subagent_thread_id": "f1", "status": "failed", "result": "short error"},
            {"subagent_thread_id": "c1", "status": "completed", "result": "a" * 4000},
            {"subagent_thread_id": "c2", "status": "completed", "result": "b" * 4000},
        ]
        out = format_subagent_results_text(results, result_max_chars=5000, total_budget_chars=3000)
        self.assertIn("子代理 f1 执行失败：\nshort error", out)
        # completed 被压缩（每条 4000 字符 → 均分约 (3000-可渲染 failed 行长)/2）
        self.assertNotIn("a" * 4000, out)
        self.assertNotIn("b" * 4000, out)
        self.assertIn("已截断", out)

    def test_total_budget_placeholder_when_fragment(self):
        """均分预算低于最小保留字符数 → completed 替换为占位符。"""
        from Django_xm.apps.agent_hub.subagent_tools.wait import (
            MIN_SUMMARY_KEEP_CHARS,
            TRUNCATED_PLACEHOLDER,
            format_subagent_results_text,
        )

        results = [
            {"subagent_thread_id": "f1", "status": "failed", "result": "e" * (2000 - 500)},
            {"subagent_thread_id": "c1", "status": "completed", "result": "a" * 900},
            {"subagent_thread_id": "c2", "status": "completed", "result": "b" * 900},
            {"subagent_thread_id": "c3", "status": "completed", "result": "c" * 900},
        ]
        out = format_subagent_results_text(
            results, result_max_chars=5000, total_budget_chars=MIN_SUMMARY_KEEP_CHARS * 2
        )
        self.assertIn("子代理 f1 执行失败", out)
        self.assertIn(TRUNCATED_PLACEHOLDER, out)

    def test_total_budget_extreme_fallback_compresses_failed(self):
        """极端场景（failed 群体超总预算）：failed 也参与均分压缩，不超预算输出。"""
        from Django_xm.apps.agent_hub.subagent_tools.wait import format_subagent_results_text

        results = [
            {"subagent_thread_id": "f1", "status": "failed", "result": "e" * 5000},
            {"subagent_thread_id": "f2", "status": "failed", "result": "f" * 5000},
        ]
        out = format_subagent_results_text(results, result_max_chars=5000, total_budget_chars=1000)
        self.assertNotIn("e" * 5000, out)
        self.assertNotIn("f" * 5000, out)
        self.assertIn("已截断", out)

    def test_empty_results_placeholder(self):
        from Django_xm.apps.agent_hub.subagent_tools.wait import format_subagent_results_text

        self.assertEqual(
            format_subagent_results_text([], result_max_chars=100, total_budget_chars=1000),
            "子代理结果缺失",
        )


class TestWaitForSubAgentArun(unittest.TestCase):
    """_arun 行为：interrupt payload / 非法 resume / 超时 resume。"""

    def _run_arun(self, instances: dict, interrupt_return=mock.sentinel.NONE, ids=None):
        from Django_xm.apps.agent_hub.subagent_tools.wait import wait_for_subagent

        ids = ids or ["s1", "s2"]
        runtime = _make_runtime(instances)
        with mock.patch(f"{_RUNTIME_MODULE}.get_subagent_runtime", return_value=runtime), mock.patch(
            "langgraph.types.interrupt", return_value=interrupt_return
        ) as interrupt_mock:
            out = asyncio.run(wait_for_subagent._arun(list(ids)))
        return out, runtime, interrupt_mock

    def test_all_terminal_short_circuit(self):
        """竞态兜底：全部已终态 → 不挂起直接返回（经预算压缩）。"""
        instances = {
            "s1": SimpleNamespace(status="completed", result_preview="done-1"),
            "s2": SimpleNamespace(status="failed", result_preview="boom"),
        }
        out, _runtime, interrupt_mock = self._run_arun(instances)
        interrupt_mock.assert_not_called()
        self.assertIn("done-1", out)
        self.assertIn("boom", out)

    def test_interrupt_payload_carries_deadline(self):
        """interrupt payload 携带 deadline（epoch 秒，当前时刻 + 配置超时）。"""
        import time

        from Django_xm.apps.agent_hub.subagent_tools.wait import (
            get_wait_timeout_seconds,
            wait_for_subagent,
        )

        runtime = _make_runtime({"s1": SimpleNamespace(status="running", result_preview="")})
        before = time.time()
        with mock.patch(f"{_RUNTIME_MODULE}.get_subagent_runtime", return_value=runtime), mock.patch(
            "langgraph.types.interrupt", return_value={"subagent_results": []}
        ) as interrupt_mock:
            asyncio.run(wait_for_subagent._arun(["s1"]))
        payload = interrupt_mock.call_args.args[0]
        self.assertTrue(payload["_subagent_wait"])
        self.assertEqual(payload["subagent_thread_ids"], ["s1"])
        expected_deadline = before + get_wait_timeout_seconds()
        self.assertGreaterEqual(payload["deadline"], expected_deadline - 5)
        self.assertLessEqual(payload["deadline"], time.time() + get_wait_timeout_seconds() + 5)

    def test_invalid_resume_reports_real_states(self):
        """非法 resume：逐 id 查 DB 报真实状态（不再伪造 failed），unknown 附原始值。"""
        instances = {
            "s1": SimpleNamespace(status="running", result_preview=""),
            "s2": SimpleNamespace(status="completed", result_preview="ok-result"),
            "s3": None,
        }
        out, _runtime, _ = self._run_arun(instances, interrupt_return="junk-resume", ids=["s1", "s2", "s3"])
        self.assertIn("非法 resume 值", out)
        self.assertIn("junk-resume", out)
        self.assertIn("子代理 s1 未完成（状态：running）", out)
        self.assertIn("子代理 s2 已完成", out)
        self.assertIn("ok-result", out)
        self.assertIn("子代理 s3 未完成（状态：unknown）", out)
        self.assertIn("未找到子代理记录（原始 resume 值：'junk-resume'）", out)

    def test_timeout_resume_reports_real_states(self):
        """超时 resume（_subagent_wait_timeout 标记）：走失败路径报真实状态。"""
        timeout_resume = {"_subagent_wait_timeout": True, "deadline": 1234.0, "waited_seconds": 7200}
        instances = {
            "s1": SimpleNamespace(status="running", result_preview=""),
            "s2": SimpleNamespace(status="completed", result_preview="late-result"),
        }
        out, _runtime, _ = self._run_arun(instances, interrupt_return=timeout_resume)
        self.assertIn("等待子代理超时", out)
        self.assertIn("deadline=1234.0", out)
        self.assertIn("子代理 s1 未完成（状态：running）", out)
        self.assertIn("late-result", out)

    def test_normal_resume_formats_results(self):
        """正常 resume：按预算格式化全部结果。"""
        resume_value = {
            "subagent_results": [
                {"subagent_thread_id": "s1", "status": "completed", "result": "r1"},
                {"subagent_thread_id": "s2", "status": "failed", "result": "e1"},
            ]
        }
        out, _, _ = self._run_arun(
            {"s1": SimpleNamespace(status="running", result_preview="")},
            interrupt_return=resume_value,
        )
        self.assertIn("r1", out)
        self.assertIn("e1", out)

    def test_empty_ids_error(self):
        from Django_xm.apps.agent_hub.subagent_tools.wait import wait_for_subagent

        out = asyncio.run(wait_for_subagent._arun([]))
        self.assertIn("缺少 subagent_thread_ids", out)


if __name__ == "__main__":
    unittest.main()
