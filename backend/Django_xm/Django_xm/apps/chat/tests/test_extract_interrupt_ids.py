"""extract_interrupt_ids 与 langgraph_resume_id 传播单测（P0 回归保障）。

背景：
    问题22/问题24 的根因是 ``effective_resume_key`` 在 ``chat_resume_generator.py``
    中回退到了 ``interrupt_id``（tool_call_id），而非 LangGraph 的实际 interrupt ID。
    这是因为 ``langgraph_resume_id`` 未被正确持久化到 ``Approval.extra``。

    LangGraph 1.1.10 中 ``interrupt(value)`` 创建的 ``Interrupt`` 对象的 ``id``
    默认为 ``'placeholder-id'``，但当 LangGraph 内部调用时（携带 ns 命名空间），
    ``id`` 会被设为 ``xxh3_128_hexdigest("|".join(ns).encode())`` ——一个基于命名空间的确定性哈希。

    本测试验证：
    1. ``extract_interrupt_ids`` 从真实 ``Interrupt`` 对象正确提取 ``intr.id`` 作为 ``langgraph_resume_id``
    2. ``extract_interrupt_ids`` 从 dict 形态正确提取 ``id``
    3. ``parse_approval_interrupt`` 将 ``langgraph_resume_id`` 透传到每个 ``approval_data``
    4. ``_build_chat_approval_sync_payload`` 将 ``langgraph_resume_id`` 写入 ``extra``
    5. 批量场景下所有 approval 共享同一个 ``langgraph_resume_id``（同一 interrupt）

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python -m pytest Django_xm/apps/chat/tests/test_extract_interrupt_ids.py -v
"""

from __future__ import annotations

import os
import unittest

# Django 环境初始化
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from langgraph.types import Interrupt

from Django_xm.apps.chat.services.stream.loop import _build_chat_approval_sync_payload
from Django_xm.apps.chat.services.stream_helpers import (
    extract_interrupt_ids,
    parse_approval_interrupt,
)

# 模拟 LangGraph 内部为 interrupt 生成的命名空间哈希（xxh3_128_hexdigest）
LANGGRAPH_INTR_ID = "8ff982f53224928365dcd4b8ced73a50"
BATCH_ID = "batch-uuid-from-middleware-abc123"


def _make_batch_interrupt_value(requests: list[dict]) -> dict:
    """构造 ApprovalMiddleware 产生的批量 interrupt_value。"""
    return {
        "_approval": True,
        "requests": requests,
        "_meta": {"graph_interrupt_id": BATCH_ID},
    }


def _make_request(tool_name: str, tool_call_id: str, *, operation: str = "ls -la") -> dict:
    return {
        "_approval": True,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "graph_interrupt_id": BATCH_ID,
        "session_id": "session-1",
        "title": f"确认执行 {tool_name}",
        "description": "",
        "operation": operation,
        "danger_level": "high",
        "risk_level": "controlled",
        "args": {"command": operation},
    }


class ExtractInterruptIdsTests(unittest.TestCase):
    """extract_interrupt_ids 三字段提取测试（P0 核心）。"""

    def test_interrupt_object_with_namespace_hash_id(self):
        """真实 Interrupt 对象：intr.id 为命名空间哈希，应作为 langgraph_resume_id 返回。"""
        interrupt_value = _make_batch_interrupt_value([_make_request("shell_exec", "tc-1")])
        intr = Interrupt(value=interrupt_value, id=LANGGRAPH_INTR_ID)

        value, graph_id, resume_id = extract_interrupt_ids(intr)

        self.assertIs(value, interrupt_value)
        # graph_interrupt_id 优先从 _meta 读取（middleware UUID）
        self.assertEqual(graph_id, BATCH_ID)
        # langgraph_resume_id = intr.id（命名空间哈希），用于 Command(resume=...)
        self.assertEqual(resume_id, LANGGRAPH_INTR_ID)

    def test_dict_form_with_id_key(self):
        """dict 形态 {"value": ..., "id": ...}：id 字段作为 langgraph_resume_id。"""
        interrupt_value = _make_batch_interrupt_value([_make_request("shell_exec", "tc-1")])
        intr_dict = {"value": interrupt_value, "id": LANGGRAPH_INTR_ID}

        value, graph_id, resume_id = extract_interrupt_ids(intr_dict)

        self.assertEqual(value, interrupt_value)
        self.assertEqual(graph_id, BATCH_ID)
        self.assertEqual(resume_id, LANGGRAPH_INTR_ID)

    def test_dict_form_without_id_key_returns_empty_resume_id(self):
        """dict 形态无 id 键：langgraph_resume_id 为空（会导致 resume 回退到 tool_call_id）。"""
        interrupt_value = _make_batch_interrupt_value([_make_request("shell_exec", "tc-1")])
        intr_dict = {"value": interrupt_value}  # 无 id 键

        value, graph_id, resume_id = extract_interrupt_ids(intr_dict)

        self.assertEqual(value, interrupt_value)
        self.assertEqual(graph_id, BATCH_ID)
        # 无 id → langgraph_resume_id 为空（这是问题22/24 的根因场景）
        self.assertEqual(resume_id, "")

    def test_interrupt_object_default_placeholder_id(self):
        """Interrupt 对象使用默认 id='placeholder-id' 时也应正确提取。"""
        interrupt_value = _make_batch_interrupt_value([_make_request("shell_exec", "tc-1")])
        intr = Interrupt(value=interrupt_value)  # id 默认 'placeholder-id'

        _, graph_id, resume_id = extract_interrupt_ids(intr)

        self.assertEqual(resume_id, "placeholder-id")
        self.assertEqual(graph_id, BATCH_ID)

    def test_graph_interrupt_id_falls_back_to_intr_id_when_no_meta(self):
        """无 _meta 时 graph_interrupt_id 回退到 intr.id。"""
        # 非 _approval 格式的 interrupt（无 _meta.graph_interrupt_id）
        intr = Interrupt(value={"some": "value"}, id=LANGGRAPH_INTR_ID)

        _value, graph_id, resume_id = extract_interrupt_ids(intr)

        self.assertEqual(resume_id, LANGGRAPH_INTR_ID)
        # 无 _meta → graph_id 回退到 intr_id
        self.assertEqual(graph_id, LANGGRAPH_INTR_ID)


class LanggraphResumeIdPropagationTests(unittest.TestCase):
    """langgraph_resume_id 从 extract_interrupt_ids → parse_approval_interrupt → sync_payload 的完整传播。"""

    def test_parse_approval_interrupt_propagates_langgraph_resume_id(self):
        """parse_approval_interrupt 将 langgraph_resume_id 透传到每个 approval_data。"""
        requests = [
            _make_request("shell_exec", "tc-shell-1"),
            _make_request("shell_exec", "tc-shell-2"),
        ]
        interrupt_value = _make_batch_interrupt_value(requests)

        result = parse_approval_interrupt(
            interrupt_value,
            graph_interrupt_id=BATCH_ID,
            langgraph_resume_id=LANGGRAPH_INTR_ID,
        )

        self.assertEqual(len(result), 2)
        for item in result:
            # 每个 approval_data 都应携带 langgraph_resume_id（同一 interrupt 共享）
            self.assertEqual(item["langgraph_resume_id"], LANGGRAPH_INTR_ID)
            # graph_interrupt_id 是批次 ID（middleware UUID）
            self.assertEqual(item["graph_interrupt_id"], BATCH_ID)
            # interrupt_id 是 tool_call_id（前端 resume 端点用）
            self.assertIn(item["interrupt_id"], ["tc-shell-1", "tc-shell-2"])

    def test_parse_approval_interrupt_empty_langgraph_resume_id_propagated(self):
        """langgraph_resume_id 为空时也透传（空字符串，不省略字段）。"""
        requests = [_make_request("shell_exec", "tc-1")]
        interrupt_value = _make_batch_interrupt_value(requests)

        result = parse_approval_interrupt(
            interrupt_value,
            graph_interrupt_id=BATCH_ID,
            langgraph_resume_id="",  # 空字符串（根因场景）
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["langgraph_resume_id"], "")

    def test_build_chat_approval_sync_payload_persists_langgraph_resume_id(self):
        """_build_chat_approval_sync_payload 将 langgraph_resume_id 写入 extra（P0 核心断言）。

        这是最关键的断言：如果 langgraph_resume_id 未写入 extra，
        chat_resume_generator.py 的 effective_resume_key 会回退到 tool_call_id，
        导致问题22/24 的"interrupt_id 校验不通过"。
        """
        requests = [_make_request("shell_exec", "tc-shell-1", operation="ollama list")]
        interrupt_value = _make_batch_interrupt_value(requests)

        approval_data_list = parse_approval_interrupt(
            interrupt_value,
            graph_interrupt_id=BATCH_ID,
            langgraph_resume_id=LANGGRAPH_INTR_ID,
        )
        self.assertEqual(len(approval_data_list), 1)
        approval_data = approval_data_list[0]

        data = {
            "session_id": "session-1",
            "_assistant_message_id": 999,
            "mode": "agent",
            "use_tools": True,
            "use_web_search": False,
            "use_mcp": False,
            "selected_mcp_servers": [],
            "selected_tools": ["shell_exec"],
            "use_knowledge_base": False,
            "selected_knowledge_bases": [],
            "tool_tier": "standard",
            "provider_id": "deepseek",
            "model_name": "deepseek-v4",
            "use_deep_thinking": False,
            "special_params": None,
            "temperature": 0.7,
            "max_tokens": 4096,
        }

        payload = _build_chat_approval_sync_payload(
            approval_data=approval_data,
            session_id="session-1",
            message_id="999",
            data=data,
        )

        extra = payload.get("extra", {})
        # P0 核心断言：langgraph_resume_id 必须被持久化到 extra
        self.assertEqual(
            extra.get("langgraph_resume_id"),
            LANGGRAPH_INTR_ID,
            "langgraph_resume_id 必须持久化到 extra，否则 resume 时 effective_resume_key "
            "回退到 tool_call_id，导致问题22/24 的 interrupt_id 校验失败",
        )
        # graph_interrupt_id 也应被持久化（批次 ID）
        self.assertEqual(extra.get("graph_interrupt_id"), BATCH_ID)
        # tool_config / model_config 也应存在
        self.assertIn("tool_config", extra)
        self.assertIn("model_config", extra)

    def test_build_chat_approval_sync_payload_empty_resume_id_when_not_extracted(self):
        """当 extract_interrupt_ids 返回空 langgraph_resume_id 时，extra 中也为空。

        这个测试 documenting 了问题22/24 的根因场景：如果 intr 是无 id 的 dict，
        langgraph_resume_id 为空，extra.langgraph_resume_id 也为空，
        resume 时 effective_resume_key 回退到 tool_call_id。
        """
        requests = [_make_request("shell_exec", "tc-1")]
        interrupt_value = _make_batch_interrupt_value(requests)

        # 模拟 intr 无 id 的场景（langgraph_resume_id=""）
        approval_data_list = parse_approval_interrupt(
            interrupt_value,
            graph_interrupt_id=BATCH_ID,
            langgraph_resume_id="",
        )
        approval_data = approval_data_list[0]

        data = {
            "session_id": "session-1",
            "mode": "agent",
            "use_tools": True,
            "selected_tools": ["shell_exec"],
            "provider_id": "deepseek",
            "model_name": "deepseek-v4",
        }

        payload = _build_chat_approval_sync_payload(
            approval_data=approval_data,
            session_id="session-1",
            message_id="1",
            data=data,
        )

        extra = payload.get("extra", {})
        # 根因场景：langgraph_resume_id 为空（会导致 resume 校验失败）
        self.assertEqual(extra.get("langgraph_resume_id"), "")


class FullPropagationIntegrationTests(unittest.TestCase):
    """从 Interrupt 对象 → extract_interrupt_ids → parse → sync_payload 的完整传播集成测试。"""

    def test_full_propagation_with_real_interrupt_object(self):
        """端到端：真实 Interrupt 对象 → langgraph_resume_id 正确传播到 extra。"""
        requests = [
            _make_request("shell_exec", "tc-shell-1", operation="ollama list"),
            _make_request("shell_exec", "tc-shell-2", operation="ollama ps"),
        ]
        interrupt_value = _make_batch_interrupt_value(requests)
        # 模拟 LangGraph 内部创建的 Interrupt（id 为命名空间哈希）
        intr = Interrupt(value=interrupt_value, id=LANGGRAPH_INTR_ID)

        # Step 1: extract_interrupt_ids
        value, graph_id, resume_id = extract_interrupt_ids(intr)
        self.assertEqual(resume_id, LANGGRAPH_INTR_ID)
        self.assertEqual(graph_id, BATCH_ID)

        # Step 2: parse_approval_interrupt
        approval_data_list = parse_approval_interrupt(
            value,
            graph_interrupt_id=graph_id,
            langgraph_resume_id=resume_id,
        )
        self.assertEqual(len(approval_data_list), 2)

        # Step 3: 每个审批的 langgraph_resume_id 都等于 intr.id
        for approval_data in approval_data_list:
            self.assertEqual(approval_data["langgraph_resume_id"], LANGGRAPH_INTR_ID)

        # Step 4: sync_payload 的 extra 含正确的 langgraph_resume_id
        data = {
            "session_id": "session-1",
            "mode": "agent",
            "use_tools": True,
            "selected_tools": ["shell_exec"],
            "provider_id": "deepseek",
            "model_name": "deepseek-v4",
        }
        for approval_data in approval_data_list:
            payload = _build_chat_approval_sync_payload(
                approval_data=approval_data,
                session_id="session-1",
                message_id="1",
                data=data,
            )
            extra = payload["extra"]
            self.assertEqual(
                extra["langgraph_resume_id"],
                LANGGRAPH_INTR_ID,
                f"审批 {approval_data['tool_call_id']} 的 extra.langgraph_resume_id 必须等于 intr.id",
            )


if __name__ == "__main__":
    unittest.main()
