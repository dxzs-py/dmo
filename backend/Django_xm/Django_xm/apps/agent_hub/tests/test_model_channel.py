"""AgentConfig.model 通道封闭单元测试（spec: unify-llm-fallback-single-chain，Task 5.3 + 5.5）。

覆盖：
A. 通道封闭（AgentConfig.validate，Task 5.3）：model 字段禁止外部传入，
   BaseChatModel 实例与字符串均被拒绝；正常构造（model=None + provider_id /
   model_name）通过校验；异常消息明确指向"AgentFactory 内部填充 / 禁止外部传入"。
B. Factory 输出通道（AgentFactory.create）：create() 经 resolve_model 单次解析
   并写回 config.model（builder 收到的即填充后的同一 config 对象）；
   外部传入 model 时在任何 IO（checkpointer / resolve_model / builder）之前被拒绝。
C. resolve_model 参数契约（Task 5.5）：provider_id / model_name / temperature /
   max_tokens / special_params 原样透传给 get_chat_model，enable_fallback 恒为 True；
   special_params 为 None 时透传 None（`special_params or None` 归一化）。

运行：
    cd backend/Django_xm
    conda activate langchain_xm
    python manage.py test Django_xm.apps.agent_hub.tests.test_model_channel --settings=Django_xm.settings.test
"""

import unittest
from unittest import mock

from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from Django_xm.apps.agent_hub.config import AgentConfig, AgentType
from Django_xm.apps.agent_hub.exceptions import ConfigValidationError
from Django_xm.apps.agent_hub.factory import AgentFactory
from Django_xm.apps.agent_hub.model_resolver import resolve_model

# resolve_model / get_async_checkpointer 均为延迟导入（函数内 import），
# patch 统一打在源模块属性上，factory 运行时取到的即为 mock。
_RESOLVE_MODEL_PATH = "Django_xm.apps.agent_hub.model_resolver.resolve_model"
_GET_ASYNC_CHECKPOINTER_PATH = "Django_xm.apps.ai_engine.services.checkpointer_factory.get_async_checkpointer"
_GET_STORE_PATH = "Django_xm.apps.ai_engine.services.checkpointer_factory.get_store"
_PREFLIGHT_CHECK_PATH = "Django_xm.apps.agent_hub.preflight.ExecutionPreflight.check"
_GET_CHAT_MODEL_PATH = "Django_xm.apps.ai_engine.services.llm_factory.get_chat_model"
_GET_PROVIDER_CONFIG_PATH = "Django_xm.apps.ai_engine.services.registry_service.get_provider_config"


def _make_model() -> BaseChatModel:
    """构造一个真实的 BaseChatModel 实例（langchain_core 官方 fake 实现）。"""
    return FakeMessagesListChatModel(responses=[AIMessage(content="pong")])


def _make_base_config(**kwargs) -> AgentConfig:
    """构造通过校验的 BASE 类型配置（model=None，走 provider_id/model_name 通道）。"""
    kwargs.setdefault("agent_type", AgentType.BASE)
    kwargs.setdefault("provider_id", "test-provider")
    kwargs.setdefault("model_name", "test-model")
    return AgentConfig(**kwargs)


class ModelChannelClosureTests(unittest.TestCase):
    """Task 5.3：AgentConfig.model 通道封闭——禁止外部传入模型实例或字符串。"""

    def test_validate_rejects_model_instance(self):
        """外部传入 BaseChatModel 实例时 validate() 拒绝（通道封闭）。"""
        config = AgentConfig(model=_make_model())

        with self.assertRaises(ConfigValidationError):
            config.validate()

    def test_validate_rejects_model_string(self):
        """外部传入模型名字符串时 validate() 同样拒绝（不允许绕过 provider 通道）。"""
        config = AgentConfig(model="gpt-4o")

        with self.assertRaises(ConfigValidationError):
            config.validate()

    def test_validate_accepts_none_model_with_provider_fields(self):
        """正常构造（model=None + provider_id/model_name）时 validate() 不抛。"""
        config = _make_base_config()

        try:
            config.validate()
        except ConfigValidationError as e:  # pragma: no cover - 断言辅助
            self.fail(f"model=None 的合法配置不应校验失败: {e}")

    def test_validate_error_message_explains_closure(self):
        """异常消息须指明封闭语义（AgentFactory 内部填充 / 禁止外部传入）。"""
        config = AgentConfig(model="gpt-4o")

        with self.assertRaises(ConfigValidationError) as ctx:
            config.validate()

        message = str(ctx.exception)
        self.assertTrue(
            "禁止外部传入" in message or "AgentFactory 内部填充" in message,
            f"异常消息应说明封闭语义，实际为: {message}",
        )


class FactoryModelFillTests(unittest.IsolatedAsyncioTestCase):
    """Task 5.3：AgentFactory.create() 的 model 输出通道（写回 + 单次解析）。"""

    def _make_fake_builder(self) -> tuple[mock.MagicMock, mock.MagicMock]:
        """构造 fake builder：async build 返回带 .graph 属性的 MagicMock。"""
        fake_agent = mock.MagicMock()  # MagicMock 自动具备 .graph 属性
        fake_builder = mock.MagicMock()
        fake_builder.build = mock.AsyncMock(return_value=fake_agent)
        return fake_builder, fake_agent

    def _patch_io_side_effects(self):
        """隔离 factory 依赖的外部 IO：store / checkpointer / preflight。

        返回嵌套 patcher 列表，供 with contextlib.ExitStack 统一进入/退出。
        """
        from contextlib import ExitStack

        stack = ExitStack()
        # resolve_defaults() 内部 get_store() 真实调用会有副作用（Store 创建），patch 为 None 跳过注入
        stack.enter_context(mock.patch(_GET_STORE_PATH, return_value=None))
        # checkpointer 异步注入：patch 为非 None 的 MagicMock，避免真实连接 DB
        stack.enter_context(mock.patch(_GET_ASYNC_CHECKPOINTER_PATH, return_value=mock.MagicMock()))
        # 预检 check()：patch 为直接通过的 AsyncMock，避免真实网络/DB 探测
        passed_result = mock.MagicMock(passed=True, issues=[], warnings=[])
        stack.enter_context(mock.patch(_PREFLIGHT_CHECK_PATH, return_value=passed_result))
        return stack

    async def test_create_fills_model_via_resolve_model_once(self):
        """create() 将 resolve_model 的结果写回 config.model，且全程仅解析一次。"""
        sentinel = object()
        fake_builder, fake_agent = self._make_fake_builder()
        config = _make_base_config()

        with self._patch_io_side_effects():
            with mock.patch(_RESOLVE_MODEL_PATH, return_value=sentinel) as resolve_model_mock:
                with mock.patch.object(
                    AgentFactory,
                    "_get_builders",
                    return_value={AgentType.BASE: fake_builder},
                ):
                    agent = await AgentFactory.create(config)

        # factory 输出通道：resolve_model 的返回值被写回 config.model（供 builder/调用方复用）
        self.assertIs(config.model, sentinel)
        # 全程单次创建：resolve_model 仅被调用一次
        resolve_model_mock.assert_called_once()
        # builder 收到的即填充后的同一 config 对象
        fake_builder.build.assert_awaited_once_with(config)
        # 非 CompiledStateGraph 但带 .graph 属性的对象原样返回
        self.assertIs(agent, fake_agent)

    async def test_create_rejects_external_model_before_any_io(self):
        """外部传入 model 时 create() 在任何 IO 之前直接拒绝（封闭在入口处生效）。"""
        fake_builder, _ = self._make_fake_builder()
        config = _make_base_config(model="gpt-4o")

        with self._patch_io_side_effects():
            with mock.patch(_RESOLVE_MODEL_PATH) as resolve_model_mock:
                with mock.patch.object(
                    AgentFactory,
                    "_get_builders",
                    return_value={AgentType.BASE: fake_builder},
                ):
                    with self.assertRaises(ConfigValidationError):
                        await AgentFactory.create(config)

        # validate() 首位检查生效：resolve_model 与 builder 均未被触达
        resolve_model_mock.assert_not_called()
        fake_builder.build.assert_not_awaited()


class ResolveModelParamsTests(unittest.TestCase):
    """Task 5.5：resolve_model → get_chat_model 的参数透传契约。"""

    def test_resolve_model_passes_full_params(self):
        """完整参数原样透传：provider/name/temperature/max_tokens/special_params + enable_fallback=True。"""
        with mock.patch(_GET_CHAT_MODEL_PATH) as get_chat_model_mock:
            with mock.patch(_GET_PROVIDER_CONFIG_PATH, return_value={}):
                config = AgentConfig(
                    provider_id="test-provider",
                    model_name="test-model",
                    temperature=0.5,
                    max_tokens=1024,
                    special_params={"foo": "bar"},
                )
                result = resolve_model(config)

        # 返回值即 get_chat_model 的产物（factory 写回 config.model 的对象）
        self.assertIs(result, get_chat_model_mock.return_value)
        get_chat_model_mock.assert_called_once()
        kwargs = get_chat_model_mock.call_args.kwargs
        self.assertEqual(kwargs["model_provider"], "test-provider")
        self.assertEqual(kwargs["model_name"], "test-model")
        self.assertEqual(kwargs["temperature"], 0.5)
        self.assertEqual(kwargs["max_tokens"], 1024)
        self.assertEqual(kwargs["special_params"], {"foo": "bar"})
        # 统一 fallback 通道：恒定启用（SDK 重试 + 候选切换 + 熔断）
        self.assertTrue(kwargs["enable_fallback"])

    def test_resolve_model_none_special_params_passes_none(self):
        """special_params=None 时经 `special_params or None` 归一化后透传 None。"""
        with mock.patch(_GET_CHAT_MODEL_PATH) as get_chat_model_mock:
            with mock.patch(_GET_PROVIDER_CONFIG_PATH, return_value={}):
                config = AgentConfig(provider_id="test-provider", model_name="test-model")
                resolve_model(config)

        kwargs = get_chat_model_mock.call_args.kwargs
        self.assertIsNone(kwargs["special_params"])

    def test_resolve_model_none_provider_and_name_pass_none(self):
        """provider_id / model_name 为 None 时透传 None（由 get_chat_model 走默认配置）。"""
        with mock.patch(_GET_CHAT_MODEL_PATH) as get_chat_model_mock:
            with mock.patch(_GET_PROVIDER_CONFIG_PATH, return_value={}):
                config = AgentConfig(temperature=0.7)
                resolve_model(config)

        kwargs = get_chat_model_mock.call_args.kwargs
        self.assertIsNone(kwargs["model_provider"])
        self.assertIsNone(kwargs["model_name"])


if __name__ == "__main__":
    unittest.main()
