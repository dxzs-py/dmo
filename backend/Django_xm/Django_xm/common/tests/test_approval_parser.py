"""approval_parser.py 单元测试。

锁定两类契约：
1. 批量格式（唯一生产者 ApprovalMiddleware）的字段透传行为——清理后与清理前
   对生产数据逐字段一致；
2. 非法/历史格式输入一律返回 []（单工具格式为已废弃历史协议，不再兼容；
   任意非批量 dict 不得被解析成审批条目）。
"""

import logging

from django.test import SimpleTestCase

from Django_xm.common.approval_parser import parse_approval_interrupt

PARSER_LOGGER = "Django_xm.common.approval_parser"


def _batch_value(requests, meta_graph_id="gi-1"):
    """构造 ApprovalMiddleware 形状的批量 interrupt 值（middleware.py L911-919）。"""
    return {
        "_approval": True,
        "requests": requests,
        "_meta": {"graph_interrupt_id": meta_graph_id},
    }


def _middleware_request(**overrides):
    """构造单个审批请求（middleware.py L794-816 形状）。

    注：批次 id 只写外层 _meta，request 内不含 graph_interrupt_id
    （死字段已删，spec approval-pipeline-fixes A5）。
    """
    req = {
        "tool_name": "shell_exec",
        "tool_call_id": "tc-1",
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

    def test_tool_call_id_missing_skipped_with_warning(self):
        """F-A（spec approval-id-fallback-refactor A1）：缺 tool_call_id 的请求被
        跳过 + WARNING，不再顶替批次 id（旧行为会导致记录合并/恢复键冲突）。"""
        req = _middleware_request()
        del req["tool_call_id"]
        with self.assertLogs(PARSER_LOGGER, level=logging.WARNING) as cm:
            result = parse_approval_interrupt(
                _batch_value([req, _middleware_request(tool_call_id="tc-2")])
            )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tool_call_id"], "tc-2")
        self.assertTrue(
            any("缺失 tool_call_id" in m for m in cm.output),
            f"期望缺失 tool_call_id 告警，实际: {cm.output}",
        )

    def test_non_dict_requests_skipped(self):
        value = _batch_value([_middleware_request(), "bad", 123, None])
        result = parse_approval_interrupt(value)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tool_call_id"], "tc-1")

    def test_langgraph_resume_id_missing_empty_with_warning(self):
        """F-B（spec A2）：langgraph_resume_id 缺省 → 字段空串 + WARNING，
        不再回退批次 uuid（该值不是合法 resume key）。"""
        with self.assertLogs(PARSER_LOGGER, level=logging.WARNING) as cm:
            entry = parse_approval_interrupt(_batch_value([_middleware_request()]))[0]
        self.assertEqual(entry["langgraph_resume_id"], "")
        self.assertTrue(
            any("langgraph_resume_id" in m for m in cm.output),
            f"期望 langgraph_resume_id 缺省告警，实际: {cm.output}",
        )

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


class LoggingObservabilityTests(SimpleTestCase):
    """日志可观测性契约（spec approval-pipeline-fixes A1/A2/A3）。"""

    def test_flagged_invalid_format_logs_warning(self):
        """带 _approval 标记但 requests 非法 = 契约漂移信号，必须 WARNING 可见。"""
        with self.assertLogs(PARSER_LOGGER, level=logging.WARNING) as cm:
            self.assertEqual(parse_approval_interrupt({"_approval": True, "requests": "x"}), [])
            self.assertEqual(parse_approval_interrupt({"_approval": True}), [])
        self.assertTrue(
            all(r.levelno == logging.WARNING for r in cm.records),
            f"期望全部 WARNING，实际: {[r.levelname for r in cm.records]}",
        )

    def test_non_approval_dict_logs_debug_only(self):
        """非审批 dict 属正常防御路径，不得触发 WARNING（防刷屏）。"""
        with self.assertLogs(PARSER_LOGGER, level=logging.DEBUG) as cm:
            self.assertEqual(parse_approval_interrupt({"requests": [], "foo": 1}), [])
        self.assertEqual(
            [r for r in cm.records if r.levelno >= logging.WARNING],
            [],
            f"非审批 dict 不应产生 WARNING，实际: {[r.levelname for r in cm.records]}",
        )

    def test_duplicate_interrupt_id_logs_warning_and_keeps_entries(self):
        """producer 重复 tool_call_id → WARNING 且条目全部保留（不去重）。

        F-A 后缺失 tool_call_id 的请求已被跳过，重复 id 只能来自 producer 重复。
        """
        req_a = _middleware_request()
        req_b = _middleware_request(tool_name="web_search")  # 同 tool_call_id="tc-1"
        with self.assertLogs(PARSER_LOGGER, level=logging.WARNING) as cm:
            result = parse_approval_interrupt(_batch_value([req_a, req_b]))
        self.assertEqual(len(result), 2)
        self.assertEqual([e["interrupt_id"] for e in result], ["tc-1", "tc-1"])
        self.assertTrue(
            any("重复 interrupt_id" in msg for msg in cm.output),
            f"期望重复 interrupt_id 告警，实际: {cm.output}",
        )

    def test_distinct_tool_call_ids_no_warning(self):
        """正常批量（tool_call_id 唯一、resume id 已传）不得触发 WARNING。

        F-B 后 langgraph_resume_id 必须由调用方显式传入（生产路径三个调用方
        均传 intr.id），缺省触发 WARNING 是新契约的预期告警。
        """
        value = _batch_value(
            [_middleware_request(), _middleware_request(tool_call_id="tc-2", tool_name="web_search")]
        )
        with self.assertNoLogs(PARSER_LOGGER, level=logging.WARNING):
            result = parse_approval_interrupt(value, langgraph_resume_id="int-1")
        self.assertEqual(len(result), 2)
        self.assertEqual([e["interrupt_id"] for e in result], ["tc-1", "tc-2"])
        self.assertEqual([e["langgraph_resume_id"] for e in result], ["int-1", "int-1"])
