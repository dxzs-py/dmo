"""official_deep_agent DEGRADE 降级路径单元测试

验证:
1. FULL → REDUCED_TOOLS 降级使用降级工具集重建 graph
2. REDUCED_TOOLS → NO_TOOLS 降级调用 _fallback_direct_answer
3. 降级工具集为空时直接回退到 _fallback_direct_answer

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python -m pytest Django_xm/apps/research/tests/test_deep_research_degrade.py -v
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Django 环境初始化（兼容 pytest 和 unittest 直接运行）
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from Django_xm.apps.agent_hub.services.agent_resilience import (
    DegradationLevel,
    ErrorAction,
)
from Django_xm.apps.research.services.adapter import (
    OfficialDeepAgentAdapter,
)

# ============================================================================
# 辅助函数
# ============================================================================


def _make_failing_astream(error: Exception | None = None):
    """构造一个立即抛出异常的 async generator astream"""
    err = error or RuntimeError("simulate astream failure")

    async def _astream(graph_input, config=None, stream_mode=None, **kwargs):
        raise err
        yield  # 使函数成为 async generator（不可达）

    return _astream


def _make_empty_astream():
    """构造一个立即结束的空 async generator astream"""

    async def _astream(graph_input, config=None, stream_mode=None, **kwargs):
        # 空流，直接结束（return 触发 StopAsyncIteration）
        return
        yield  # 使函数成为 async generator（不可达）

    return _astream


def _make_mock_graph(astream_fn=None, aget_state_values=None):
    """构造一个 mock graph

    Args:
        astream_fn: astream 实现（async generator function）
        aget_state_values: aget_state 返回的 state.values，None 表示空 state
    """
    graph = MagicMock()
    if astream_fn is not None:
        graph.astream = astream_fn

    state_mock = MagicMock()
    state_mock.values = aget_state_values if aget_state_values is not None else {}
    graph.aget_state = AsyncMock(return_value=state_mock)
    return graph


def _make_classified():
    """构造一个 mock classified 异常对象"""
    classified = MagicMock()
    classified.error_code = "TEST_ERROR"
    classified.user_message = "测试错误"
    return classified


# ============================================================================
# 测试用例
# ============================================================================


class TestDegradeFullToReduced(unittest.IsolatedAsyncioTestCase):
    """测试 FULL → REDUCED_TOOLS 降级路径"""

    async def test_full_to_reduced_rebuilds_graph_with_degraded_tools(self):
        """FULL → REDUCED_TOOLS: 使用降级工具集重建 graph，新 graph 的 astream 被调用"""
        # 原始 graph：astream 抛异常触发 DEGRADE
        original_graph = _make_mock_graph(astream_fn=_make_failing_astream())

        # 新 graph：astream 空流正常结束
        new_graph = _make_mock_graph(astream_fn=_make_empty_astream())

        # 降级工具集（非空）
        degraded_tools = [MagicMock(name="degraded_tool_1")]

        adapter = OfficialDeepAgentAdapter(
            graph=original_graph,
            thread_id="test-task-id",
            work_dir=None,
            original_config=MagicMock(),
            original_tools=[MagicMock(name="tool_1"), MagicMock(name="tool_2")],
        )

        with (
            patch(
                "Django_xm.apps.agent_hub.services.agent_resilience.classify_and_decide",
                return_value=(ErrorAction.DEGRADE, _make_classified()),
            ),
            patch(
                "Django_xm.apps.agent_hub.services.agent_resilience.get_degraded_tools",
                return_value=degraded_tools,
            ) as mock_get_degraded,
            patch(
                "Django_xm.apps.agent_hub.services.agent_resilience.calculate_backoff",
                return_value=0.0,
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch.object(
                adapter,
                "_rebuild_with_degraded_tools",
                new_callable=AsyncMock,
                return_value=new_graph,
            ) as mock_rebuild,
        ):
            result = await adapter.astream_research_with_interrupts(query="测试查询")

        # 验证 get_degraded_tools 被调用，传入原始工具和 REDUCED_TOOLS 级别
        mock_get_degraded.assert_called_once()
        call_args = mock_get_degraded.call_args
        self.assertEqual(call_args.args[1], DegradationLevel.REDUCED_TOOLS)
        self.assertEqual(len(call_args.args[0]), 2)  # 原始工具数量

        # 验证 _rebuild_with_degraded_tools 被调用，传入降级工具集
        mock_rebuild.assert_awaited_once_with(degraded_tools)

        # 验证 self.graph 已被替换为新 graph
        self.assertIs(adapter.graph, new_graph)

        # 验证新 graph 的 aget_state 被调用（说明新 graph 被用于执行）
        new_graph.aget_state.assert_awaited_once()

        # 验证结果标记为降级
        self.assertTrue(result.get("degraded"))
        self.assertEqual(result.get("degradation_level"), DegradationLevel.REDUCED_TOOLS.value)


class TestDegradeReducedToNoTools(unittest.IsolatedAsyncioTestCase):
    """测试 REDUCED_TOOLS → NO_TOOLS 降级路径"""

    async def test_reduced_to_no_tools_calls_fallback_direct_answer(self):
        """REDUCED_TOOLS → NO_TOOLS: 触发 DEGRADE 时调用 _fallback_direct_answer"""
        # 原始 graph：astream 抛异常（触发 FULL → REDUCED 重建）
        original_graph = _make_mock_graph(astream_fn=_make_failing_astream())

        # 新 graph：astream 也抛异常（触发 REDUCED → NO_TOOLS 回退）
        new_graph = _make_mock_graph(astream_fn=_make_failing_astream(RuntimeError("second failure on degraded graph")))

        degraded_tools = [MagicMock(name="degraded_tool_1")]

        adapter = OfficialDeepAgentAdapter(
            graph=original_graph,
            thread_id="test-task-id",
            work_dir=None,
            original_config=MagicMock(),
            original_tools=[MagicMock(name="tool_1")],
        )

        fallback_result = {
            "success": True,
            "query": "测试查询",
            "final_report": "fallback answer",
            "plan": None,
            "current_step": "completed",
            "error": None,
            "files": [],
            "state_files": {},
            "degraded": True,
            "degradation_level": "no_tools",
        }

        with (
            patch(
                "Django_xm.apps.agent_hub.services.agent_resilience.classify_and_decide",
                return_value=(ErrorAction.DEGRADE, _make_classified()),
            ),
            patch(
                "Django_xm.apps.agent_hub.services.agent_resilience.get_degraded_tools",
                return_value=degraded_tools,
            ),
            patch(
                "Django_xm.apps.agent_hub.services.agent_resilience.calculate_backoff",
                return_value=0.0,
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch.object(
                adapter,
                "_rebuild_with_degraded_tools",
                new_callable=AsyncMock,
                return_value=new_graph,
            ) as mock_rebuild,
            patch.object(
                adapter,
                "_fallback_direct_answer",
                new_callable=AsyncMock,
                return_value=fallback_result,
            ) as mock_fallback,
        ):
            result = await adapter.astream_research_with_interrupts(query="测试查询")

        # 验证 _rebuild_with_degraded_tools 被调用一次（FULL → REDUCED）
        mock_rebuild.assert_awaited_once()

        # 验证 _fallback_direct_answer 被调用（REDUCED → NO_TOOLS）
        mock_fallback.assert_awaited_once()
        # 验证 fallback 接收的 query 参数
        self.assertEqual(mock_fallback.call_args.args[0], "测试查询")

        # 验证返回的是 fallback 结果
        self.assertEqual(result, fallback_result)


class TestDegradeEmptyToolsFallback(unittest.IsolatedAsyncioTestCase):
    """测试降级工具集为空时直接回退"""

    async def test_empty_degraded_tools_triggers_fallback(self):
        """降级工具集为空时，不重建 graph，直接回退到 _fallback_direct_answer"""
        # 原始 graph：astream 抛异常触发 DEGRADE
        original_graph = _make_mock_graph(astream_fn=_make_failing_astream())

        adapter = OfficialDeepAgentAdapter(
            graph=original_graph,
            thread_id="test-task-id",
            work_dir=None,
            original_config=MagicMock(),
            original_tools=[MagicMock(name="tool_1")],
        )

        fallback_result = {
            "success": True,
            "query": "测试查询",
            "final_report": "fallback answer (empty tools)",
            "plan": None,
            "current_step": "completed",
            "error": None,
            "files": [],
            "state_files": {},
            "degraded": True,
            "degradation_level": "no_tools",
        }

        with (
            patch(
                "Django_xm.apps.agent_hub.services.agent_resilience.classify_and_decide",
                return_value=(ErrorAction.DEGRADE, _make_classified()),
            ),
            patch(
                "Django_xm.apps.agent_hub.services.agent_resilience.get_degraded_tools",
                return_value=[],  # 空降级工具集
            ) as mock_get_degraded,
            patch(
                "Django_xm.apps.agent_hub.services.agent_resilience.calculate_backoff",
                return_value=0.0,
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch.object(
                adapter,
                "_rebuild_with_degraded_tools",
                new_callable=AsyncMock,
            ) as mock_rebuild,
            patch.object(
                adapter,
                "_fallback_direct_answer",
                new_callable=AsyncMock,
                return_value=fallback_result,
            ) as mock_fallback,
        ):
            result = await adapter.astream_research_with_interrupts(query="测试查询")

        # 验证 get_degraded_tools 被调用
        mock_get_degraded.assert_called_once()

        # 验证 _rebuild_with_degraded_tools 未被调用（工具集为空，跳过重建）
        mock_rebuild.assert_not_awaited()

        # 验证 _fallback_direct_answer 被调用
        mock_fallback.assert_awaited_once()
        self.assertEqual(mock_fallback.call_args.args[0], "测试查询")

        # 验证返回的是 fallback 结果
        self.assertEqual(result, fallback_result)


if __name__ == "__main__":
    unittest.main()
