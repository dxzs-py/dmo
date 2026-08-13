"""LangGraph 多 pending interrupt 逐个恢复语义单元测试（方案 B 验证）。

背景
----
深度研究审批恢复（research_resume_task，Path D）在方案 B 中移除了
"其他批次 pending 防护"，改为"本批次全部决断后立即恢复本批次 interrupt"。
本测试验证该方案依赖的 LangGraph 1.x 基础语义：

1. 多个 pending interrupt 按产生顺序逐个 ``Command(resume=...)`` 恢复：
   - 已恢复的 interrupt 对应执行路径不重入历史 checkpoint（无重复执行）
   - 未恢复的 interrupt 保持 pending（``get_state().tasks`` 可见），
     agent 推进到它时再次暂停，形成串行推进，不会死锁
2. node 恢复语义：interrupt 所在 node 恢复时从头重跑，interrupt() 返回
   缓存值（首次暂停时 node 内 interrupt 之前的局部修改不入 checkpoint）。
   该语义影响深度研究 after_model（ApprovalMiddleware）：恢复重跑会重复
   执行 interrupt 前的审计/事件发布，但幂等集合（approved 才幂等）保证
   不重复拦截审批、未决断工具会再次暂停等待决策（因此"本批次全部决断
   才恢复"是必须的）。

langgraph 1.x 行为注意：``graph.invoke`` 遇 interrupt 不抛异常，返回含
``"__interrupt__"`` 字段的结果 dict；``Command(resume=...)`` 的 key
必须是 ``Interrupt.id``。

运行：本测试不依赖 Django settings/DB，直接执行
``python -m unittest Django_xm.apps.research.tests.test_interrupt_resume_semantics``
"""

import unittest

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict


class _State(TypedDict):
    """测试图状态：executed 记录 node 执行痕迹，decisions 记录 interrupt 决策。"""

    executed: list
    decisions: list


def _node_a(state: _State) -> _State:
    """模拟深度研究第一轮 after_model 中断点。"""
    state["executed"].append("A")
    dec = interrupt({"approval": "qa"})
    state["decisions"].append(("A", dec))
    return state


def _node_b(state: _State) -> _State:
    """模拟深度研究第二轮 after_model 中断点。"""
    state["executed"].append("B")
    dec = interrupt({"approval": "qb"})
    state["decisions"].append(("B", dec))
    return state


def _build_graph():
    builder = StateGraph(_State)
    builder.add_node("a", _node_a)
    builder.add_node("b", _node_b)
    builder.add_edge(START, "a")
    builder.add_edge("a", "b")
    builder.add_edge("b", END)
    return builder.compile(checkpointer=InMemorySaver())


class InterruptResumeSemanticsTests(unittest.TestCase):
    """LangGraph 多 pending interrupt 逐个恢复语义验证（方案 B 前提）。"""

    def test_sequential_resume_no_history_replay(self):
        """逐个 resume：A 不重入历史 checkpoint，qb 保持 pending，最终完成。

        流程：首次执行暂停在 qa → resume qa → 推进到 qb 暂停 → resume qb → 完成。
        """
        graph = _build_graph()
        thread = {"configurable": {"thread_id": "t1"}}

        # 1. 首次执行：暂停在 qa（返回 __interrupt__）
        r = graph.invoke({"executed": [], "decisions": []}, config=thread)
        ints = r.get("__interrupt__", [])
        self.assertEqual(len(ints), 1)
        self.assertEqual(ints[0].value, {"approval": "qa"})
        qa_id = ints[0].id

        # 2. resume qa（部分恢复）：决策写入，推进到 qb 暂停
        r = graph.invoke(Command(resume={qa_id: "yes"}), config=thread)
        ints = r.get("__interrupt__", [])
        self.assertEqual(len(ints), 1)
        self.assertEqual(ints[0].value, {"approval": "qb"})
        qb_id = ints[0].id
        snap = graph.get_state(thread)
        # A 只执行一次（无历史 checkpoint 重入）；qa 决策已写入
        self.assertEqual(snap.values["executed"].count("A"), 1)
        self.assertEqual(snap.values["decisions"][0][1], "yes")

        # 3. resume qb：完成，两轮决策全部写入
        r = graph.invoke(Command(resume={qb_id: "yes"}), config=thread)
        self.assertEqual(r.get("__interrupt__", []), [])
        snap = graph.get_state(thread)
        self.assertEqual(snap.values["decisions"], [["A", "yes"], ["B", "yes"]])
        # A 全程只执行一次（node 恢复重跑只影响 interrupt 所在 node 的
        # interrupt 前代码，已恢复的历史 node 不重入）
        self.assertEqual(snap.values["executed"].count("A"), 1)

    def test_partial_resume_keeps_other_pending(self):
        """部分恢复：未 resume 的 interrupt 保持 pending（tasks 可见）。"""
        graph = _build_graph()
        thread = {"configurable": {"thread_id": "t2"}}

        r = graph.invoke({"executed": [], "decisions": []}, config=thread)
        qa_id = r["__interrupt__"][0].id

        r = graph.invoke(Command(resume={qa_id: "yes"}), config=thread)
        snap = graph.get_state(thread)
        # qb 仍为 pending task（next 指向 b，tasks 非空）
        self.assertEqual(snap.next, ("b",))
        self.assertTrue(snap.tasks, "qb 应仍为 pending task")

    def test_resume_unknown_interrupt_id_behavior(self):
        """未知 interrupt id 恢复：LangGraph 不抛错，而是把 resume dict 整体
        作为当前 pending interrupt 的 resume value 传入（静默放行语义）。

        结论：``Command(resume=...)`` 的 key 必须来自真实 ``Interrupt.id``
        （深度研究由 interrupt 事件解析出的 langgraph_resume_id），否则误传的
        dict 会被当作决策值注入，导致审批决策失真（未决断工具被放行）。
        正常流程中 id 均来自真实 interrupt 事件，不会触发该路径。
        """
        graph = _build_graph()
        thread = {"configurable": {"thread_id": "t3"}}

        r = graph.invoke({"executed": [], "decisions": []}, config=thread)
        self.assertEqual(len(r.get("__interrupt__", [])), 1)
        # 未知 id 不抛错：qa 收到 {unknown_interrupt_id: True} 作为 resume value，
        # node_a 继续执行并推进到 node_b 的 qb interrupt 处再次暂停
        r2 = graph.invoke(Command(resume={"unknown_interrupt_id": True}), config=thread)
        self.assertEqual(len(r2.get("__interrupt__", [])), 1)
        self.assertEqual(r2["__interrupt__"][0].value, {"approval": "qb"})
        snap = graph.get_state(thread)
        self.assertEqual(snap.values["decisions"], [["A", {"unknown_interrupt_id": True}]])


if __name__ == "__main__":
    unittest.main()
