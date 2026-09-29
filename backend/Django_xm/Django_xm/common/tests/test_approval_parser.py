"""approval_parser.py 单元测试。

锁定两类契约：
1. 批量格式（唯一生产者 ApprovalMiddleware）的字段透传行为——清理后与清理前
   对生产数据逐字段一致；
2. 非法/历史格式输入一律返回 []（单工具格式为已废弃历史协议，不再兼容；
   任意非批量 dict 不得被解析成审批条目）。
"""

from django.test import SimpleTestCase

from Django_xm.common.approval_parser import parse_approval_interrupt


def _batch_value(requests, meta_graph_id="gi-1"):
    """构造 ApprovalMiddleware 形状的批量 interrupt 值（middleware.py L911-919）。"""
    return {
        "_approval": True,
        "requests": requests,
        "_meta": {"graph_interrupt_id": meta_graph_id},
    }


def _middleware_request(**overrides):
    """构造单个审批请求（middleware.py L794-816 形状）。"""
    req = {
        "tool_name": "shell_exec",
        "tool_call_id": "tc-1",
        "graph_interrupt_id": "gi-1",
        "session_id": "s-1",
        "title": "执行命令",
        "description": "执行 ls 命令",
        "operation": "ls -la",
        "danger_level": "medium",
        "risk_level": "controlled",
        "args": {"command": "ls"},
    }
    req.update(overrides)
    return req


class BatchFormatPassthroughTests(SimpleTestCase):
    """批量格式字段透传（与清理前生产行为一致的回归基线）。"""

    def test_full_fields_passthrough(self):
        result = parse_approval_interrupt(
            _batch_value([_middleware_request()]),
            graph_interrupt_id="g-call",
            langgraph_resume_id="lr-1",
        )
        self.assertEqual(len(result), 1)
        entry = result[0]
        self.assertEqual(entry["tool_name"], "shell_exec")
        self.assertEqual(entry["tool_call_id"], "tc-1")
        self.assertEqual(entry["interrupt_id"], "tc-1")
        # _meta.graph_interrupt_id 优先于调用方传入值
        self.assertEqual(entry["graph_interrupt_id"], "gi-1")
        self.assertEqual(entry["langgraph_resume_id"], "lr-1")
        self.assertEqual(entry["title"], "执行命令")
        self.assertEqual(entry["description"], "执行 ls 命令")
        self.assertEqual(entry["operation"], "ls -la")
        self.assertEqual(entry["danger_level"], "medium")
        self.assertEqual(entry["risk_level"], "controlled")
        self.assertEqual(entry["parameters"], {"command": "ls"})
        # 生产者不产 action，恒为默认值
        self.assertEqual(entry["action"], "confirm")

    def test_dead_fields_not_emitted(self):
        """清理的死字段不得再出现在输出中。"""
        result = parse_approval_interrupt(
            _batch_value([_middleware_request(extra={"x": 1}, command="old-cmd")])
        )
        entry = result[0]
        for key in ("state", "extra", "input_placeholder", "position", "args", "command"):
            self.assertNotIn(key, entry)

    def test_subagent_fields_with_depth_zero(self):
        req = _middleware_request(
            parent_tool_call_id="p-1",
            depth=0,
            agent_name="researcher",
            agent_path=["root", "researcher"],
            subagent_thread_id="sub-1",
        )
        entry = parse_approval_interrupt(_batch_value([req]))[0]
        self.assertEqual(entry["parent_tool_call_id"], "p-1")
        # depth=0 是合法值（middleware L813 默认 0），不得丢失
        self.assertEqual(entry["depth"], 0)
        self.assertEqual(entry["agent_name"], "researcher")
        self.assertEqual(entry["agent_path"], ["root", "researcher"])
        self.assertEqual(entry["subagent_thread_id"], "sub-1")

    def test_empty_subagent_fields_skipped(self):
        req = _middleware_request(
            parent_tool_call_id="",
            depth=0,
            agent_name="",
            agent_path=[],
            subagent_thread_id="",
        )
        entry = parse_approval_interrupt(_batch_value([req]))[0]
        self.assertEqual(entry["depth"], 0)
        for key in ("parent_tool_call_id", "agent_name", "agent_path", "subagent_thread_id"):
            self.assertNotIn(key, entry)

    def test_tool_call_id_missing_falls_back_to_graph_id(self):
        req = _middleware_request()
        del req["tool_call_id"]
        entry = parse_approval_interrupt(_batch_value([req]))[0]
        self.assertEqual(entry["interrupt_id"], "gi-1")
        self.assertEqual(entry["tool_call_id"], "gi-1")

    def test_non_dict_requests_skipped(self):
        value = _batch_value([_middleware_request(), "bad", 123, None])
        result = parse_approval_interrupt(value)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tool_call_id"], "tc-1")

    def test_langgraph_resume_id_falls_back_to_graph_id(self):
        entry = parse_approval_interrupt(_batch_value([_middleware_request()]))[0]
        self.assertEqual(entry["langgraph_resume_id"], "gi-1")

    def test_caller_graph_id_used_when_meta_empty(self):
        """_meta 存在但不含 graph_interrupt_id 键时，回退调用方传入值。"""
        value = {"_approval": True, "requests": [_middleware_request()], "_meta": {}}
        entry = parse_approval_interrupt(value, graph_interrupt_id="g-call")[0]
        self.assertEqual(entry["graph_interrupt_id"], "g-call")

    def test_multiple_requests(self):
        value = _batch_value(
            [_middleware_request(), _middleware_request(tool_call_id="tc-2", tool_name="web_search")]
        )
        result = parse_approval_interrupt(value)
        self.assertEqual([e["interrupt_id"] for e in result], ["tc-1", "tc-2"])


class RejectionContractTests(SimpleTestCase):
    """非法/历史格式输入一律返回 []（新契约）。"""

    def test_non_dict_returns_empty(self):
        for bad in (None, "x", 123, [1], ("a",)):
            with self.subTest(value=bad):
                self.assertEqual(parse_approval_interrupt(bad), [])

    def test_missing_approval_flag_rejected(self):
        """无 _approval 标识的任意 dict 不得被解析成审批条目。"""
        self.assertEqual(parse_approval_interrupt({"requests": [], "foo": 1}), [])

    def test_legacy_single_tool_format_rejected(self):
        """历史单工具格式（生产者 interrupt_for_approval 已删除）返回空。"""
        legacy = {
            "_approval": True,
            "tool_name": "shell_exec",
            "interrupt_id": "i-1",
            "title": "t",
            "args": {"command": "ls"},
        }
        self.assertEqual(parse_approval_interrupt(legacy), [])

    def test_requests_not_list_rejected(self):
        self.assertEqual(parse_approval_interrupt({"_approval": True, "requests": "x"}), [])
        self.assertEqual(parse_approval_interrupt({"_approval": True}), [])

    def test_empty_requests_returns_empty(self):
        self.assertEqual(parse_approval_interrupt(_batch_value([])), [])
