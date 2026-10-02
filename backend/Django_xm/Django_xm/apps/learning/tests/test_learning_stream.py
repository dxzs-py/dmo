"""learning_stream（SSE 流式服务）与 _model_helper（节点模型工厂）单元测试（ARCH-21）。

覆盖：
- iter_workflow_events：custom/values 事件分类、start 步骤去重、
  waiting_for_answers 中断终止、publish 回调、非元组事件兼容、空事件跳过
- pump_sync_events：线程泵投递与 EOF/错误哨兵
- safe_publish_workflow_event：payload 组装与异常吞掉
- emit_step：非流式上下文下安全 no-op
- _extract_model_kwargs / get_chat_model_from_state / get_structured_model_from_state

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.learning.tests.test_learning_stream --noinput
"""

import asyncio
import os
from unittest import TestCase as UnitTestCase
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from Django_xm.apps.learning.nodes.stream_events import emit_step
from Django_xm.apps.learning.services._model_helper import (
    _extract_model_kwargs,
    get_chat_model_from_state,
    get_structured_model_from_state,
)
from Django_xm.apps.learning.services.learning_stream import (
    iter_workflow_events,
    pump_sync_events,
    safe_publish_workflow_event,
)
from Django_xm.common.event_schema import EventType

_LS = "Django_xm.apps.learning.services.learning_stream"


def _values_event(step: str, **extra) -> tuple:
    """values 模式事件（多 stream_mode 下为 (mode, chunk) 元组）"""
    return ("values", {"current_step": step, **extra})


class IterWorkflowEventsTests(UnitTestCase):
    """iter_workflow_events：事件流分类与转换"""

    def _run(self, events):
        """在 mock stream_with_resilience 下收集事件与 publish 回调"""
        publish = mock.Mock()
        with mock.patch(f"{_LS}.stream_with_resilience", return_value=iter(list(events))):
            collected = list(iter_workflow_events(study_flow=mock.Mock(), initial_state={}, config={},
                                                 user_id=1, thread_id="t-stream", publish=publish))
        return collected, publish

    def test_custom_event_becomes_workflow_step(self):
        """custom 事件（节点内 emit_step）→ workflow_step"""
        collected, publish = self._run([("custom", {"step": "planner", "message": "正在生成学习计划..."})])
        self.assertEqual(collected, [{"type": "workflow_step", "data": {"step": "planner",
                                                                       "message": "正在生成学习计划..."}}])
        publish.assert_called_once_with(EventType.WORKFLOW_STEP, {"step": "planner",
                                                                  "message": "正在生成学习计划..."})

    def test_start_step_suppresses_step_event(self):
        """初始状态（current_step=start）只发 state_update，不发 step 事件"""
        collected, publish = self._run([_values_event("start")])
        types = [e["type"] for e in collected]
        self.assertEqual(types, ["workflow_state_update"])
        publish.assert_called_once_with(EventType.WORKFLOW_STATE_UPDATE, {"step": "start", "state": "start"})

    def test_values_event_yields_step_then_state(self):
        """values 事件（非 start）→ 先 workflow_step 后 workflow_state_update"""
        collected, publish = self._run([_values_event("planner", learning_plan={"topic": "闭包"})])
        types = [e["type"] for e in collected]
        self.assertEqual(types, ["workflow_step", "workflow_state_update"])
        state_event = collected[1]["data"]
        self.assertEqual(state_event["learning_plan"], {"topic": "闭包"})
        self.assertEqual(state_event["step"], "planner")
        self.assertEqual(publish.call_count, 2)

    def test_waiting_for_answers_terminates_stream(self):
        """waiting_for_answers → step + state_update + interrupted 后停止"""
        collected, _ = self._run([
            _values_event("planner"),
            _values_event("waiting_for_answers", quiz={"total_points": 10}),
            _values_event("grading"),  # 不应到达
        ])
        types = [e["type"] for e in collected]
        self.assertEqual(types, ["workflow_step", "workflow_state_update", "workflow_step",
                                 "workflow_state_update", "interrupted"])
        self.assertEqual(collected[-1], {"type": "interrupted", "data": {}})

    def test_bare_dict_treated_as_values(self):
        """非元组事件（单 stream_mode 兼容）按 values 处理"""
        collected, _ = self._run([{"current_step": "grading", "score": 80}])
        self.assertEqual(collected[0]["type"], "workflow_step")
        self.assertEqual(collected[0]["data"]["step"], "grading")

    def test_falsy_events_skipped(self):
        """空事件被跳过"""
        collected, _ = self._run([None, _values_event("grading")])
        self.assertEqual(len(collected), 2)

    def test_unknown_step_uses_default_message(self):
        """未知步骤使用兜底文案"""
        collected, _ = self._run([("custom", {"step": "mystery"})])
        self.assertEqual(collected[0]["data"]["message"], "处理中...")


class PumpSyncEventsTests(UnitTestCase):
    """pump_sync_events：同步生成器线程泵"""

    def test_pumps_events_then_eof(self):
        """事件逐条投递，正常结束后投递 None 哨兵"""
        loop, queue = mock.Mock(), mock.Mock()
        events = [{"type": "workflow_step"}, {"type": "interrupted"}]
        pump_sync_events(iter(events), loop, queue)
        calls = loop.call_soon_threadsafe.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0].args, (queue.put_nowait, events[0]))
        self.assertEqual(calls[1].args, (queue.put_nowait, events[1]))
        self.assertEqual(calls[2].args, (queue.put_nowait, None))

    def test_error_event_on_exception(self):
        """迭代异常 → 投递 error 事件 + None 哨兵"""

        def _broken():
            yield {"type": "workflow_step"}
            raise RuntimeError("graph boom")

        loop, queue = mock.Mock(), mock.Mock()
        pump_sync_events(_broken(), loop, queue)
        calls = loop.call_soon_threadsafe.call_args_list
        self.assertEqual(len(calls), 3)
        error_event = calls[1].args[1]
        self.assertEqual(error_event["type"], "error")
        self.assertIn("失败", error_event["data"]["message"])
        self.assertIs(calls[2].args[1], None)


class SafePublishWorkflowEventTests(UnitTestCase):
    """safe_publish_workflow_event：SSE 流中同步推送 WS 事件"""

    def test_publishes_merged_payload(self):
        """payload 合并 source / source_id / thread_id"""
        with mock.patch(f"{_LS}.publish_event", new=mock.AsyncMock()) as publish:
            asyncio.run(safe_publish_workflow_event(EventType.WORKFLOW_STEP, "t-1", {"step": "planner"},
                                                    user_id=7))
        publish.assert_awaited_once()
        args, kwargs = publish.await_args
        payload = args[1]
        self.assertEqual(payload["source"], "learning")
        self.assertEqual(payload["source_id"], "t-1")
        self.assertEqual(payload["thread_id"], "t-1")
        self.assertEqual(payload["step"], "planner")
        self.assertEqual(kwargs["task_id"], "t-1")
        self.assertEqual(kwargs["user_id"], "7")

    def test_swallows_publish_errors(self):
        """发布失败被吞掉（不影响 SSE 主流程）"""
        with mock.patch(f"{_LS}.publish_event", new=mock.AsyncMock(side_effect=RuntimeError("ws down"))):
            result = asyncio.run(safe_publish_workflow_event(EventType.WORKFLOW_STEP, "t-1", {}))
        self.assertIsNone(result)


class EmitStepTests(UnitTestCase):
    """emit_step：节点内实时步骤事件"""

    def test_safe_outside_stream_context(self):
        """非流式上下文（graph.invoke）下调用安全，不抛异常"""
        emit_step("grading", "正在评分...")
        # 无异常即通过；非流式上下文下 get_stream_writer 为 no-op 或抛错均被吞掉


class ExtractModelKwargsTests(UnitTestCase):
    """_extract_model_kwargs：仅提取非空运行时配置"""

    def test_empty_state_returns_empty(self):
        """空 state → 空参数（不覆盖工厂默认值）"""
        self.assertEqual(_extract_model_kwargs({}), {})

    def test_full_state_extracts_all(self):
        """全量配置提取"""
        state = {
            "provider_id": "deepseek",
            "model_name": "deepseek-chat",
            "temperature": 0.7,
            "max_tokens": 2048,
            "special_params": {"thinking": True},
        }
        self.assertEqual(
            _extract_model_kwargs(state),
            {
                "model_provider": "deepseek",
                "model_name": "deepseek-chat",
                "temperature": 0.7,
                "max_tokens": 2048,
                "special_params": {"thinking": True},
            },
        )

    def test_none_and_falsy_fields_excluded(self):
        """None / 空串 / 空 dict 不进入参数"""
        state = {"provider_id": "", "model_name": None, "temperature": None, "max_tokens": None,
                 "special_params": {}}
        self.assertEqual(_extract_model_kwargs(state), {})


class ModelFactoryFromStateTests(UnitTestCase):
    """get_chat_model_from_state / get_structured_model_from_state"""

    def test_chat_model_forwards_kwargs(self):
        """聊天模型工厂转发提取参数"""
        state = {"provider_id": "openai", "temperature": 0.2}
        with mock.patch("Django_xm.apps.learning.services._model_helper.get_chat_model") as factory:
            get_chat_model_from_state(state, streaming=True)
        factory.assert_called_once_with(model_provider="openai", temperature=0.2, streaming=True)

    def test_structured_model_defaults_non_streaming(self):
        """结构化输出默认 streaming=False（流式 + 结构化嵌套冲突防护）"""
        with mock.patch(
            "Django_xm.apps.learning.services._model_helper.get_structured_model_with_fallback"
        ) as factory:
            get_structured_model_from_state({}, schema=dict)
        factory.assert_called_once_with(dict, streaming=False)

    def test_structured_model_explicit_streaming_preserved(self):
        """显式传入 streaming 时不被默认值覆盖"""
        with mock.patch(
            "Django_xm.apps.learning.services._model_helper.get_structured_model_with_fallback"
        ) as factory:
            get_structured_model_from_state({"model_name": "m"}, schema=dict, streaming=True)
        factory.assert_called_once_with(dict, model_name="m", streaming=True)

    def test_structured_model_forwards_runtime_config(self):
        """结构化输出工厂转发 state 运行时配置"""
        state = {"provider_id": "deepseek", "special_params": {"thinking": True}}
        with mock.patch(
            "Django_xm.apps.learning.services._model_helper.get_structured_model_with_fallback"
        ) as factory:
            get_structured_model_from_state(state, schema=dict)
        factory.assert_called_once_with(
            dict, model_provider="deepseek", special_params={"thinking": True}, streaming=False
        )
