"""Skill 步骤级审批门禁测试（fix-review-round3 1.2）。

覆盖：
- 门禁单元语义：SAFE 步骤直接放行（只读步骤不弹审批）；图外调用
  （无 checkpointer 上下文）fail-closed 拒绝 CONTROLLED 步骤
- 图内全链路：工具内 interrupt 挂起 → payload 为 middleware 同构批量格式
  （携带 skill_name/step_idx）→ parser 解析 → Command(resume) 恢复 →
  批准后继续执行 / 拒绝后技能终止
- 管道集成：SkillBaseTool（SAFE + CONTROLLED 混合管道）只对 CONTROLLED
  步骤挂起审批，批准后管道继续执行完成；拒绝则终止
- 恢复重放去重：经审批步骤重放命中结果缓存，不重复执行（副作用去重）

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.tools.tests.test_skill_step_approval --noinput
"""

import asyncio
import os
import uuid
from typing import Annotated, TypedDict

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command

from django.core.cache import cache
from django.test import SimpleTestCase

from Django_xm.apps.agent_hub.approval.step_gate import (
    SKILL_STEP_CACHE_PREFIX,
    build_skill_step_interrupt_id,
    gate_skill_step_tool_call,
)
from Django_xm.apps.tools.skills.registry import SkillSpec, SkillStep
from Django_xm.apps.tools.skills.tool import SkillBaseTool

EXEC_COUNTER: dict = {"count": 0}


from langchain_core.tools import BaseTool  # noqa: E402


class EvilSideEffectTool(BaseTool):
    name: str = "evil_side_effect"
    description: str = "需要审批的副作用工具（测试用）"

    def _run(self, target: str = "") -> str:
        return f"evil-executed:{target}"


class CountingTool(BaseTool):
    name: str = "counting_tool"
    description: str = "计数工具（重放去重断言用，未注册策略 → CONTROLLED）"

    def _run(self, target: str = "") -> str:
        EXEC_COUNTER["count"] += 1
        return f"exec-{EXEC_COUNTER['count']}:{target}"


class SafeCalculatorTool(BaseTool):
    name: str = "calculator"
    description: str = "只读白名单工具（SAFE 自动通过）"

    def _run(self, expression: str = "") -> str:
        return "calc-done"


def _make_graph_state():
    class S(TypedDict):
        messages: Annotated[list, add_messages]

    return S


async def _run_to_interrupt(graph, config) -> tuple[list[dict], str]:
    """运行图直到挂起，返回 (updates chunks, interrupt id)。"""
    chunks = []
    interrupt_id = ""
    async for chunk in graph.astream({"messages": []}, config, stream_mode="updates"):
        chunks.append(chunk)
        if isinstance(chunk, dict) and "__interrupt__" in chunk:
            interrupt_id = chunk["__interrupt__"][0].id
    return chunks, interrupt_id


async def _run_resume(graph, config, interrupt_id: str, resume_value) -> list[dict]:
    chunks = []
    async for chunk in graph.astream(
        Command(resume={interrupt_id: resume_value}), config, stream_mode="updates"
    ):
        chunks.append(chunk)
    return chunks


class SkillStepGateUnitTests(SimpleTestCase):
    """门禁单元语义（图外调用）。"""

    def test_safe_step_allowed_without_interrupt(self):
        """SAFE 工具（只读白名单 calculator）直接放行，不发起审批。"""
        result = asyncio.run(
            gate_skill_step_tool_call("calculator", {"expression": "1+1"}, skill_name="s", step_idx=0)
        )
        self.assertTrue(result.allowed)
        self.assertFalse(result.requires_result_cache)
        self.assertIsNone(result.cached_result)

    def test_controlled_step_fail_closed_outside_graph(self):
        """图外调用（无 checkpointer/interrupt 上下文）：CONTROLLED 步骤 fail-closed 拒绝。"""
        result = asyncio.run(
            gate_skill_step_tool_call(
                "fs_write_file", {"relative_path": "a.md"}, skill_name="s", step_idx=1
            )
        )
        self.assertFalse(result.allowed)
        self.assertIn("审批门禁不可用", result.message)

    def test_step_interrupt_id_prefix_isolated_from_tool_call_ids(self):
        """step interrupt_id 前缀隔离，与真实 tool_call_id 空间无冲突。"""
        interrupt_id = build_skill_step_interrupt_id("demo_skill", 2)
        self.assertTrue(interrupt_id.startswith("skillstep-demo_skill-2-"))


class SkillStepGateGraphTests(SimpleTestCase):
    """图内全链路：工具内 interrupt → 批量格式 payload → 恢复决策。"""

    def _build(self, tool_name: str = "fs_write_file", args: dict | None = None):
        captured: dict = {}
        state = _make_graph_state()

        async def node(state):
            gate = await gate_skill_step_tool_call(
                tool_name, args or {"relative_path": "out.md"}, skill_name="demo_skill", step_idx=1
            )
            captured["gate"] = gate
            return {"messages": [f"allowed={gate.allowed}"]}

        graph_builder = StateGraph(state)
        graph_builder.add_node("node", node)
        graph_builder.add_edge(START, "node")
        graph_builder.add_edge("node", END)
        graph = graph_builder.compile(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": str(uuid.uuid4()), "chat_session_id": "sess-g"}}
        return graph, config, captured

    def test_interrupt_payload_is_middleware_batch_format_with_skill_context(self):
        """挂起 payload 为批量格式，请求携带 skill_name/step_idx；parser 可解析。"""
        graph, config, _ = self._build()
        chunks, interrupt_id = asyncio.run(_run_to_interrupt(graph, config))
        self.assertTrue(interrupt_id, "CONTROLLED 步骤应触发审批挂起")

        intr_value = chunks[-1]["__interrupt__"][0].value
        self.assertTrue(intr_value.get("_approval"))
        self.assertEqual(len(intr_value["requests"]), 1)
        request = intr_value["requests"][0]
        self.assertEqual(request["tool_name"], "fs_write_file")
        self.assertEqual(request["skill_name"], "demo_skill")
        self.assertEqual(request["step_idx"], 1)
        self.assertTrue(request["tool_call_id"].startswith("skillstep-demo_skill-1-"))

        from Django_xm.common.approval_parser import parse_approval_interrupt

        entries = parse_approval_interrupt(intr_value, "", interrupt_id)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["skill_name"], "demo_skill")
        self.assertEqual(entries[0]["step_idx"], 1)
        self.assertEqual(entries[0]["langgraph_resume_id"], interrupt_id)

    def test_approved_resume_continues(self):
        """批准后恢复：gate 放行且要求写结果缓存。"""
        graph, config, captured = self._build()
        chunks, interrupt_id = asyncio.run(_run_to_interrupt(graph, config))
        step_id = chunks[-1]["__interrupt__"][0].value["requests"][0]["tool_call_id"]

        resume_chunks = asyncio.run(_run_resume(graph, config, interrupt_id, {step_id: True}))
        gate = captured["gate"]
        self.assertTrue(gate.allowed)
        self.assertTrue(gate.requires_result_cache)
        self.assertIn("allowed=True", str(resume_chunks[-1]))

    def test_rejected_resume_terminates(self):
        """拒绝后恢复：gate 不放行，message 注明用户拒绝。"""
        graph, config, captured = self._build()
        chunks, interrupt_id = asyncio.run(_run_to_interrupt(graph, config))
        step_id = chunks[-1]["__interrupt__"][0].value["requests"][0]["tool_call_id"]

        asyncio.run(_run_resume(graph, config, interrupt_id, {step_id: False}))
        gate = captured["gate"]
        self.assertFalse(gate.allowed)
        self.assertIn("拒绝", gate.message)


class SkillPipelineApprovalTests(SimpleTestCase):
    """管道集成：SkillBaseTool 在图内经审批挂起/恢复；重放去重。"""

    def setUp(self):
        EXEC_COUNTER["count"] = 0
        cache.clear()
        self.addCleanup(cache.clear)

    def _build_graph(self, skill: SkillBaseTool, captured: dict):
        state = _make_graph_state()

        async def node(state):
            result = await skill.ainvoke({"query": "run"})
            captured["result"] = result
            return {"messages": [str(result)]}

        graph_builder = StateGraph(state)
        graph_builder.add_node("node", node)
        graph_builder.add_edge(START, "node")
        graph_builder.add_edge("node", END)
        graph = graph_builder.compile(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": str(uuid.uuid4())}}
        return graph, config

    def _make_skill(self, steps: list[tuple[str, dict]], tools: list) -> SkillBaseTool:
        spec = SkillSpec(
            name="pipeline_demo",
            description="测试管道",
            steps=[SkillStep(tool_name=tn, args_template=tpl) for tn, tpl in steps],
        )
        return SkillBaseTool(spec=spec, available_tools=tools)

    def test_mixed_pipeline_only_interrupts_controlled_step_and_completes(self):
        """SAFE + CONTROLLED 混合管道：只对 CONTROLLED 步骤挂起审批，
        批准后管道继续执行完成（验收：批准后继续 / 只读步骤不弹审批）。"""
        skill = self._make_skill(
            [
                ("calculator", {"expression": "1+1"}),
                ("evil_side_effect", {"target": "{query}"}),
            ],
            [SafeCalculatorTool(), EvilSideEffectTool()],
        )
        captured: dict = {}
        graph, config = self._build_graph(skill, captured)

        chunks, interrupt_id = asyncio.run(_run_to_interrupt(graph, config))
        intr = chunks[-1]["__interrupt__"][0]
        requests = intr.value["requests"]
        # 只有 CONTROLLED 步骤进入审批批次（SAFE 的 calculator 不弹审批）
        self.assertEqual([r["tool_name"] for r in requests], ["evil_side_effect"])
        self.assertTrue(requests[0]["tool_call_id"].startswith("skillstep-pipeline_demo-1-"))

        step_id = requests[0]["tool_call_id"]
        asyncio.run(_run_resume(graph, config, interrupt_id, {step_id: True}))
        self.assertIn("evil-executed:run", captured["result"], "批准后管道应继续执行完成")

    def test_rejected_step_terminates_pipeline(self):
        """拒绝后管道终止，返回含步骤序号的终止文案，步骤未执行。"""
        skill = self._make_skill(
            [("evil_side_effect", {"target": "{query}"})],
            [EvilSideEffectTool()],
        )
        captured: dict = {}
        graph, config = self._build_graph(skill, captured)

        chunks, interrupt_id = asyncio.run(_run_to_interrupt(graph, config))
        step_id = chunks[-1]["__interrupt__"][0].value["requests"][0]["tool_call_id"]
        asyncio.run(_run_resume(graph, config, interrupt_id, {step_id: False}))
        self.assertIn("未获执行许可", captured["result"])
        self.assertIn("步骤 0", captured["result"])
        self.assertNotIn("evil-executed", captured["result"])

    def test_replay_uses_cached_result_for_approved_steps(self):
        """恢复重放：已审批步骤命中结果缓存跳过执行（副作用去重），并继续挂起后续审批步骤。"""
        skill = self._make_skill(
            [
                ("counting_tool", {"target": "{query}"}),
                ("evil_side_effect", {"target": "{query}"}),
            ],
            [CountingTool(), EvilSideEffectTool()],
        )
        captured: dict = {}
        graph, config = self._build_graph(skill, captured)

        chunks, interrupt_id = asyncio.run(_run_to_interrupt(graph, config))
        step0_id = chunks[-1]["__interrupt__"][0].value["requests"][0]["tool_call_id"]
        self.assertIn("skillstep-pipeline_demo-0-", step0_id)

        # 首轮挂起时 step0 尚未执行（先审批后执行）
        self.assertEqual(EXEC_COUNTER["count"], 0)

        # 模拟 step0 已批准并执行（结果已入缓存），恢复重放
        cache.set(f"{SKILL_STEP_CACHE_PREFIX}{step0_id}", "exec-1:run", 60)

        # 以 resume 重入：step0 命中缓存跳过执行 → step1 发起新审批
        chunks2, interrupt_id2 = asyncio.run(_resume_to_interrupt(graph, config, interrupt_id, {step0_id: True}))
        self.assertTrue(chunks2 and "__interrupt__" in chunks2[-1], "重放应挂起 step1 审批")
        requests = chunks2[-1]["__interrupt__"][0].value["requests"]
        self.assertEqual([r["tool_name"] for r in requests], ["evil_side_effect"])
        self.assertIn("skillstep-pipeline_demo-1-", requests[0]["tool_call_id"])
        # 重放轮 step0 未重复执行（缓存命中跳过）
        self.assertEqual(EXEC_COUNTER["count"], 0)
        _ = interrupt_id2


async def _resume_to_interrupt(graph, config, prev_interrupt_id: str, resume_value) -> tuple[list[dict], str]:
    """以 resume 重入并运行到下一个挂起，返回 (chunks, new interrupt id)。"""
    chunks = []
    interrupt_id = ""
    async for chunk in graph.astream(Command(resume={prev_interrupt_id: resume_value}), config, stream_mode="updates"):
        chunks.append(chunk)
        if isinstance(chunk, dict) and "__interrupt__" in chunk:
            interrupt_id = chunk["__interrupt__"][0].id
    return chunks, interrupt_id


if __name__ == "__main__":
    import unittest

    unittest.main()
