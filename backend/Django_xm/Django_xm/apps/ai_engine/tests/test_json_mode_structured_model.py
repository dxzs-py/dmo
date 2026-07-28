"""JsonModeStructuredModel 单元测试

验证:
1. 正常 JSON 输出能正确解析为 Pydantic 实例
2. 无效 JSON 返回 None（触发上层重试）
3. markdown 代码块包裹的 JSON 能正确解析
4. 空内容返回 None

运行方式:
    python manage.py test ai_engine.tests.test_json_mode_structured_model
"""

import json
from unittest.mock import MagicMock

from django.test import SimpleTestCase
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from Django_xm.apps.ai_engine.services.llm_factory import JsonModeStructuredModel


class TestSchema(PydanticBaseModel):
    """测试用 schema"""
    name: str = Field(description="名称")
    age: int = Field(description="年龄")


class JsonModeStructuredModelTestCase(SimpleTestCase):
    """JsonModeStructuredModel 测试"""

    def _make_model(self, response_content: str):
        """构造测试实例

        Args:
            response_content: mock 模型返回的 content
        """
        mock_chat_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = response_content
        mock_chat_model.invoke.return_value = mock_response
        mock_chat_model.ainvoke = MagicMock(return_value=mock_response)

        return JsonModeStructuredModel(
            model=mock_chat_model,
            schema=TestSchema,
            provider="deepseek",
            model_name="deepseek-v4-flash",
        ), mock_chat_model

    def test_parse_valid_json(self):
        """正常 JSON 输出能正确解析为 Pydantic 实例"""
        json_content = json.dumps({"name": "张三", "age": 25}, ensure_ascii=False)
        model, mock_chat_model = self._make_model(json_content)

        result = model.invoke([{"role": "user", "content": "test"}])

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "张三")
        self.assertEqual(result.age, 25)
        # 验证注入了 schema 提示词
        call_args = mock_chat_model.invoke.call_args
        messages = call_args[0][0]
        self.assertEqual(len(messages), 2)  # SystemMessage + 原始消息
        self.assertIn("JSON Schema", messages[0].content)

    def test_parse_markdown_wrapped_json(self):
        """markdown 代码块包裹的 JSON 能正确解析"""
        json_content = '```json\n{"name": "李四", "age": 30}\n```'
        model, _ = self._make_model(json_content)

        result = model.invoke([{"role": "user", "content": "test"}])

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "李四")
        self.assertEqual(result.age, 30)

    def test_invalid_json_returns_none(self):
        """无效 JSON 返回 None（触发上层重试）"""
        model, _ = self._make_model("这不是 JSON")

        result = model.invoke([{"role": "user", "content": "test"}])

        self.assertIsNone(result)

    def test_empty_content_returns_none(self):
        """空内容返回 None"""
        model, _ = self._make_model("")

        result = model.invoke([{"role": "user", "content": "test"}])

        self.assertIsNone(result)

    def test_schema_validation_failure_returns_none(self):
        """schema 验证失败（字段缺失）返回 None"""
        # 缺少 age 字段
        json_content = json.dumps({"name": "王五"}, ensure_ascii=False)
        model, _ = self._make_model(json_content)

        result = model.invoke([{"role": "user", "content": "test"}])

        self.assertIsNone(result)

    def test_ainvoke_works(self):
        """ainvoke 异步调用能正确解析

        JsonModeStructuredModel.ainvoke 内部仅 await self._model.ainvoke，
        不直接使用 asyncio 模块。这里用 asyncio.run + AsyncMock 验证完整异步路径。
        """
        import asyncio
        from unittest.mock import AsyncMock

        json_content = json.dumps({"name": "赵六", "age": 40}, ensure_ascii=False)
        mock_chat_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = json_content
        mock_chat_model.ainvoke = AsyncMock(return_value=mock_response)

        model = JsonModeStructuredModel(
            model=mock_chat_model,
            schema=TestSchema,
            provider="deepseek",
            model_name="deepseek-v4-flash",
        )

        result = asyncio.run(model.ainvoke([{"role": "user", "content": "test"}]))

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "赵六")
        self.assertEqual(result.age, 40)

    def test_single_message_input(self):
        """非 list 输入（单条消息）能正确处理

        _build_messages 对非 list 输入会包装为 [input]，
        验证此分支正确工作。
        """
        json_content = json.dumps({"name": "孙七", "age": 35}, ensure_ascii=False)
        model, mock_chat_model = self._make_model(json_content)

        # 传入单条消息（非 list）
        from langchain_core.messages import HumanMessage
        single_msg = HumanMessage(content="test")

        result = model.invoke(single_msg)

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "孙七")
        # 验证注入了 schema 提示词（SystemMessage + 原始消息 = 2 条）
        call_args = mock_chat_model.invoke.call_args
        messages = call_args[0][0]
        self.assertEqual(len(messages), 2)

    def test_model_invoke_exception_propagates(self):
        """模型调用抛异常时异常向上传播（由上层 ResilientInvoker 捕获）

        JsonModeStructuredModel 不吞没模型调用异常，
        交由 ResilientModel + ResilientInvoker 统一处理重试/降级。
        """
        mock_chat_model = MagicMock()
        mock_chat_model.invoke.side_effect = RuntimeError("API 连接超时")

        model = JsonModeStructuredModel(
            model=mock_chat_model,
            schema=TestSchema,
            provider="deepseek",
            model_name="deepseek-v4-flash",
        )

        # 异常应向上传播，不被吞没
        with self.assertRaises(RuntimeError):
            model.invoke([{"role": "user", "content": "test"}])

    def test_multi_messages_schema_injection_position(self):
        """多条 messages 输入时 schema 提示词注入在首位

        验证 schema 提示词作为 SystemMessage 注入到 messages 列表开头，
        原始消息顺序保持不变。
        """
        json_content = json.dumps({"name": "周八", "age": 28}, ensure_ascii=False)
        model, mock_chat_model = self._make_model(json_content)

        messages_input = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "生成数据"},
        ]

        result = model.invoke(messages_input)

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "周八")

        # 验证注入位置：SystemMessage(schema) + 原始 2 条 = 3 条
        call_args = mock_chat_model.invoke.call_args
        passed_messages = call_args[0][0]
        self.assertEqual(len(passed_messages), 3)
        # 第一条是 schema 提示词
        from langchain_core.messages import SystemMessage
        self.assertIsInstance(passed_messages[0], SystemMessage)
        self.assertIn("JSON Schema", passed_messages[0].content)
        # 后两条是原始消息
        self.assertEqual(passed_messages[1], messages_input[0])
        self.assertEqual(passed_messages[2], messages_input[1])
