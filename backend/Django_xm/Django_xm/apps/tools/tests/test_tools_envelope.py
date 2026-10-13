"""CT-03 / CT-04 / digest 遗留⑥ 工具结果注入信封测试。

B4 规则：进入模型上下文的外部来源内容（MCP 工具输出 / 联网搜索快照 /
聊天附件正文）须经 ``wrap_external_content`` 包裹隔离。既有消费面
（file_reader / web_fetch）之外，本批接入四处：
- MCP 工具（CT-03）：tools/__init__._wrap_mcp_tools_external_content
- tavily web_search（CT-04）：web_search.WebSearchTool._run
- duckduckgo_search（CT-04）：web_duckduckgo.DuckDuckGoSearchTool._run
- attachment_reader / read_multiple_attachments（遗留⑥）：经
  file_reader.read_uploaded_attachment 唯一内容出口统一包裹

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.tools.tests.test_tools_envelope --noinput
"""

import importlib
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from Django_xm.apps.tools import _wrap_mcp_tools_external_content
from Django_xm.apps.tools.langchain.external_content import EXTERNAL_CONTENT_END, EXTERNAL_CONTENT_START
from Django_xm.apps.tools.langchain import web_duckduckgo

# 注意：apps.tools.langchain.__init__ 把工具实例（web_search/file_reader）re-export
# 为与子模块同名，from-import 拿到的是实例而非模块——测试用 importlib 直取模块
web_search = importlib.import_module("Django_xm.apps.tools.langchain.web_search")
file_reader = importlib.import_module("Django_xm.apps.tools.langchain.file_reader")

_TOOLS_PKG = "Django_xm.apps.tools"


class _DummyInput(BaseModel):
    query: str = Field(description="查询")


class _DummyTool(BaseTool):
    name: str = "dummy_mcp"
    description: str = "dummy tool"
    args_schema: type[BaseModel] = _DummyInput
    metadata: dict = Field(default_factory=lambda: {"mcp_server_name": "srv1", "tier": "extended"})

    def _run(self, query: str) -> str:
        return f"raw output for {query}"


class WrapExternalContentEnvelopeTests(unittest.TestCase):
    """信封结构锚定（与 test_external_content_isolation 同口径）。"""

    def test_envelope_markers_present(self):
        wrapped = f"{EXTERNAL_CONTENT_START.format(source='x')}\nbody\n{EXTERNAL_CONTENT_END}"
        self.assertIn("[外部数据开始 | 来源: x]", wrapped)
        self.assertIn("[外部数据结束]", wrapped)


class McpToolEnvelopeTests(unittest.TestCase):
    """CT-03：MCP 工具结果统一包裹。"""

    def test_wrapped_tool_output_has_envelope(self):
        wrapped_list = _wrap_mcp_tools_external_content([_DummyTool()])
        self.assertEqual(len(wrapped_list), 1)
        wrapped = wrapped_list[0]

        # name/description 保持（工具路由与 LLM 选择不受影响）
        self.assertEqual(wrapped.name, "dummy_mcp")
        self.assertEqual(wrapped.description, "dummy tool")
        # 元数据透传（mcp_server_name 等供注册/审批链消费）
        self.assertEqual(wrapped.metadata.get("mcp_server_name"), "srv1")

        output = wrapped.invoke({"query": "hello"})
        self.assertIn("[外部数据开始 | 来源: MCP 工具]", output)
        self.assertIn("raw output for hello", output)
        self.assertIn("[外部数据结束]", output)

    def test_unwrap_failures_keep_original_tool(self):
        """包裹异常时降级保留原工具（不阻断装配）。"""
        broken = mock.MagicMock(spec=["name"])  # 缺 description/args_schema 等属性
        broken.name = "broken"

        with mock.patch(
            f"{_TOOLS_PKG}._ExternalContentWrappedTool",
            side_effect=RuntimeError("boom"),
        ):
            result = _wrap_mcp_tools_external_content([broken])

        self.assertIs(result[0], broken)

    def test_all_mcp_tools_get_wrapped(self):
        """装配点接线：每个 MCP 工具都被 wrapper 包裹（inner 指向原工具）。"""
        tools = [_DummyTool(), _DummyTool(name="mcp_b", description="b")]
        wrapped = _wrap_mcp_tools_external_content(tools)
        self.assertEqual(len(wrapped), 2)
        self.assertEqual([w.name for w in wrapped], ["dummy_mcp", "mcp_b"])
        for original, wrapper in zip(tools, wrapped):
            self.assertIsNot(original, wrapper)
            self.assertIs(wrapper.inner, original)


class WebSearchEnvelopeTests(unittest.TestCase):
    """CT-04：tavily 搜索快照包裹，错误分支不包裹。"""

    def test_list_results_wrapped(self):
        tool = web_search.WebSearchTool()
        fake = mock.MagicMock()
        fake.invoke.return_value = [{"title": "T", "url": "https://x", "content": "body text"}]

        with mock.patch.object(web_search, "create_tavily_search_tool", return_value=fake):
            output = tool._run("查询")

        self.assertIn("[外部数据开始 | 来源: 网页搜索 tavily]", output)
        self.assertIn("body text", output)
        self.assertIn("[外部数据结束]", output)

    def test_error_branch_not_wrapped(self):
        """搜索失败文案为系统生成（非外部内容），不进信封。"""
        tool = web_search.WebSearchTool()
        fake = mock.MagicMock()
        fake.invoke.side_effect = RuntimeError("quota exceeded")

        with mock.patch.object(web_search, "create_tavily_search_tool", return_value=fake):
            output = tool._run("查询")

        self.assertIn("搜索失败", output)
        self.assertNotIn("[外部数据开始", output)


class DuckDuckGoEnvelopeTests(unittest.TestCase):
    """CT-04：ddg 两条搜索路径均包裹，错误分支不包裹。"""

    def test_ddgs_path_wrapped(self):
        with mock.patch.object(web_duckduckgo, "_search_via_ddgs", return_value="搜索结果：\n\n1. x"):
            output = web_duckduckgo.DuckDuckGoSearchTool()._run("查询")

        self.assertIn("[外部数据开始 | 来源: 网页搜索 DuckDuckGo]", output)
        self.assertIn("1. x", output)
        self.assertIn("[外部数据结束]", output)

    def test_langchain_fallback_path_wrapped(self):
        with (
            mock.patch.object(web_duckduckgo, "_search_via_ddgs", side_effect=RuntimeError("ddgs down")),
            mock.patch.object(web_duckduckgo, "_search_via_langchain", return_value="fallback results"),
        ):
            output = web_duckduckgo.DuckDuckGoSearchTool()._run("查询")

        self.assertIn("[外部数据开始 | 来源: 网页搜索 DuckDuckGo]", output)
        self.assertIn("fallback results", output)

    def test_error_branch_not_wrapped(self):
        with (
            mock.patch.object(web_duckduckgo, "_search_via_ddgs", side_effect=RuntimeError("ddgs down")),
            mock.patch.object(
                web_duckduckgo, "_search_via_langchain", side_effect=RuntimeError("fallback down")
            ),
        ):
            output = web_duckduckgo.DuckDuckGoSearchTool()._run("查询")

        self.assertIn("搜索失败", output)
        self.assertNotIn("[外部数据开始", output)


class AttachmentEnvelopeTests(unittest.TestCase):
    """遗留⑥：read_uploaded_attachment 出口包裹 → attachment_reader /
    read_multiple_attachments 两工具同批覆盖。"""

    def test_read_uploaded_attachment_wrapped(self):
        # file.path 用真实存在的临时文件，避免 patch 全局 os.path.exists
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"irrelevant")
            tmp_path = tmp.name
        self.addCleanup(os.unlink, tmp_path)

        attachment = mock.MagicMock()
        attachment.original_name = "report.pdf"
        attachment.file.path = tmp_path

        with (
            mock.patch(
                "Django_xm.apps.attachments.services.cross_app.get_attachment_by_id",
                return_value=attachment,
            ),
            mock.patch.object(file_reader, "read_file_content", return_value="attachment body"),
        ):
            output = file_reader.read_uploaded_attachment(3, user=7)

        self.assertIn("[外部数据开始 | 来源: 附件 report.pdf]", output)
        self.assertIn("attachment body", output)
        self.assertIn("[外部数据结束]", output)

    def test_read_multiple_attachments_success_parts_wrapped(self):
        """批量读取的成功部分经 read_uploaded_attachment 已包裹。"""
        with mock.patch.object(
            file_reader,
            "read_uploaded_attachment",
            return_value="[外部数据开始 | 来源: 附件 a.txt]\nbody\n[外部数据结束]",
        ):
            output = file_reader.read_multiple_attachments([1, 2])

        self.assertEqual(output.count("[外部数据开始"), 2)


if __name__ == "__main__":
    unittest.main()
