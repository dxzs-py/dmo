"""跨事件循环实例缓存禁令回归测试（eliminate-priority-debts 阶段 1）。

不变量：**async 模型实例（及其 httpx/openai 异步客户端）永不跨事件循环复用**。

多 loop 架构：chat 主链跑 uvicorn 常驻 loop；每子代理独立线程独立 loop
（用完即关）。进程级实例缓存（factory LRU / _helper_model_cache 单例 /
embedding 两层实例缓存）会让"在旧 loop 中首次使用的实例"被新 loop 复用——
async 客户端连接池绑定"首次使用时的 loop"，旧 loop 已关闭 →
"Event loop is closed"（2026-10-05 实测事故：DeepSeek 402 降级 ollama 失效）。

测试模式：asyncio.run 两次模拟两个先后关闭的 loop。loop A 中构建实例并
ainvoke（模拟 async 客户端首用绑定），loop B 中再次构建并 ainvoke：
- 修复前（进程级实例缓存）：返回同一实例 → loop B 报
  RuntimeError("Event loop is closed")，或同实例断言失败 → FAIL（缺口证明）
- 修复后（即时构建）：每次全新实例 → PASS

运行（backend/Django_xm，conda env langchain_xm）：
    python manage.py test Django_xm.apps.ai_engine.services.tests.test_cross_loop_no_instance_cache --noinput
"""

import asyncio
import importlib
import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from Django_xm.apps.ai_engine.services import embedding_factory, llm_factory
from Django_xm.apps.ai_engine.services.llm_fallback import LazyFallbackChatModel

# 假注册表：openai provider_id 与 provider 字符串同名（直传命中）
_FAKE_REGISTRY = {
    "openai": {
        "provider": "openai",
        "label": "OpenAI",
        "models": [{"name": "gpt-fake", "capabilities": []}],
        "model_names": ["gpt-fake"],
        "default_model": "gpt-fake",
        "api_key_attr": "openai_api_key",
        "base_url_attr": None,
        "special_params": {},
    },
}


def _fake_registry_callables():
    registry = {pid: dict(cfg) for pid, cfg in _FAKE_REGISTRY.items()}
    get_registry = mock.MagicMock(return_value=registry)
    get_provider_config = mock.MagicMock(side_effect=lambda pid: registry.get(pid, {}))
    return registry, get_registry, get_provider_config


def _make_loop_bound_model() -> tuple[BaseChatModel, dict]:
    """构建模拟"async 客户端首用绑定事件循环"行为的聊天模型替身。

    ainvoke（经 _agenerate）首次执行时记录当前运行中的 loop；此后若在另一个
    （已关闭的）loop 中再次执行，抛 RuntimeError("Event loop is closed")
    ——复现 2026-10-05 事故机制。状态用闭包字典承载（pydantic 禁任意实例属性）。
    """
    state = {"bound_loop": None, "ainvoke_calls": 0}

    class _LoopBoundModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "loop-bound-fake"

        def _generate(
            self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs
        ) -> ChatResult:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

        async def _agenerate(
            self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs
        ) -> ChatResult:
            state["ainvoke_calls"] += 1
            current = asyncio.get_running_loop()
            if state["bound_loop"] is not None and state["bound_loop"] is not current:
                raise RuntimeError("Event loop is closed")
            state["bound_loop"] = current
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    return _LoopBoundModel(), state


def _make_loop_bound_embeddings() -> tuple[Embeddings, dict]:
    """构建模拟"async 客户端首用绑定事件循环"行为的 Embeddings 替身。"""
    state = {"bound_loop": None}

    class _LoopBoundEmbeddings(Embeddings):
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] for _ in texts]

        def embed_query(self, text: str) -> list[float]:
            return [0.0]

        async def aembed_query(self, text: str) -> list[float]:
            current = asyncio.get_running_loop()
            if state["bound_loop"] is not None and state["bound_loop"] is not current:
                raise RuntimeError("Event loop is closed")
            state["bound_loop"] = current
            return [0.0]

    return _LoopBoundEmbeddings(), state


class ChatModelFactoryCrossLoopTests(unittest.TestCase):
    """任务 1.2：factory 实例缓存退役——同 key 构建必须每次新建。"""

    def setUp(self):
        registry, get_registry, get_provider_config = _fake_registry_callables()
        patches = [
            mock.patch.object(llm_factory, "get_model_registry", get_registry),
            mock.patch.object(llm_factory, "get_provider_config", get_provider_config),
            mock.patch.object(llm_factory, "PROVIDER_REGISTRY", registry),
            mock.patch.object(llm_factory, "get_rate_limiter", return_value=None),
            mock.patch.object(llm_factory, "get_system_default_chat_model", return_value=None),
            mock.patch.object(llm_factory.settings, "openai_api_key", "test-openai-key"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_same_key_build_returns_new_instance_per_call_across_loops(self):
        """同 key 两次构建（两个先后关闭的 loop）→ 不同实例且 loop B ainvoke 成功。

        修复前：进程级实例缓存返回同一实例，loop B 中 ainvoke 报
        RuntimeError("Event loop is closed")（旧实例 client 绑定已关闭的 loop A）。
        """
        builds: list[BaseChatModel] = []

        with mock.patch.object(llm_factory, "init_chat_model") as init_mock:
            init_mock.side_effect = lambda **kwargs: _make_loop_bound_model()[0]

            async def build_and_use():
                model = llm_factory.get_chat_model_by_provider(
                    provider_id="openai", model_name="gpt-fake", temperature=0.5
                )
                builds.append(model)
                await model.ainvoke([HumanMessage("hi")])

            asyncio.run(build_and_use())  # loop A：构建 + 首用绑定，随 run 关闭
            asyncio.run(build_and_use())  # loop B：再构建 + 使用

        # 旧缓存实现：init_chat_model 仅调 1 次、两次构建同实例（回归检出力）
        self.assertEqual(init_mock.call_count, 2)
        self.assertIsNot(builds[0], builds[1])

    def test_fallback_candidates_path_builds_fresh_primary_per_call(self):
        """get_chat_model 主路径（enable_fallback=True）的 primary 同样每次新建。"""
        primaries: list[BaseChatModel] = []

        with (
            mock.patch.object(llm_factory, "init_chat_model") as init_mock,
            mock.patch.object(llm_factory, "get_fallback_candidates", return_value=[]),
        ):
            init_mock.side_effect = lambda **kwargs: _make_loop_bound_model()[0]

            for _ in range(2):
                primaries.append(
                    llm_factory.get_chat_model(
                        model_name="gpt-fake", model_provider="openai", temperature=0.5
                    )
                )

        self.assertIsNot(primaries[0], primaries[1])


class HelperModelCrossLoopTests(unittest.TestCase):
    """任务 1.3：get_helper_model 单例处置——每次调用新建。

    依据：消费点横跨 uvicorn 常驻 loop（chat_service/guardrails/compression
    async 路径）、每子代理独立线程独立 loop（research 压缩/judge）、Django
    sync 线程（learning views）——单例 wrapper 必然跨 loop 复用，违反不变量。
    """

    def setUp(self):
        registry, get_registry, get_provider_config = _fake_registry_callables()
        patches = [
            mock.patch.object(llm_factory, "get_model_registry", get_registry),
            mock.patch.object(llm_factory, "get_provider_config", get_provider_config),
            mock.patch.object(llm_factory, "PROVIDER_REGISTRY", registry),
            mock.patch.object(llm_factory, "get_rate_limiter", return_value=None),
            mock.patch.object(llm_factory, "get_system_default_chat_model", return_value=None),
            mock.patch.object(llm_factory.settings, "openai_api_key", "test-openai-key"),
            mock.patch("Django_xm.apps.ai_engine.models.SystemConfig.get_value", return_value={}),
            mock.patch("django.conf.settings.AI_HELPER_MODEL_PROVIDER", "openai", create=True),
            mock.patch("django.conf.settings.AI_HELPER_MODEL_NAME", "gpt-fake", create=True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_helper_model_rebuilt_per_call_across_loops(self):
        """无候选场景（裸 primary）：两次获取（两个 loop）→ 不同实例且 loop B ainvoke 成功。"""
        helpers: list[BaseChatModel] = []

        with (
            mock.patch.object(llm_factory, "init_chat_model") as init_mock,
            mock.patch.object(llm_factory, "get_fallback_candidates", return_value=[]),
        ):
            init_mock.side_effect = lambda **kwargs: _make_loop_bound_model()[0]

            async def get_and_use():
                helper = llm_factory.get_helper_model()
                helpers.append(helper)
                self.assertIsNotNone(helper)
                await helper.ainvoke([HumanMessage("hi")])

            asyncio.run(get_and_use())  # loop A：获取 + 首用绑定，随 run 关闭
            asyncio.run(get_and_use())  # loop B：再获取 + 使用

        # 旧单例实现：第二次返回同一实例 → loop B ainvoke 报 "Event loop is closed"
        self.assertIsNot(helpers[0], helpers[1])

    def test_helper_wrapper_rebuilt_per_call(self):
        """有候选场景：每次获取返回全新 LazyFallbackChatModel wrapper（primary 亦全新）。"""
        wrappers: list[LazyFallbackChatModel] = []

        with (
            mock.patch.object(llm_factory, "init_chat_model") as init_mock,
            mock.patch.object(
                llm_factory, "get_fallback_candidates", return_value=[("openai", "gpt-fake-b")]
            ),
        ):
            init_mock.side_effect = lambda **kwargs: _make_loop_bound_model()[0]

            for _ in range(2):
                wrapper = llm_factory.get_helper_model()
                wrappers.append(wrapper)

        for wrapper in wrappers:
            self.assertIsInstance(wrapper, LazyFallbackChatModel)
            self.assertIs(wrapper.factory, llm_factory.get_chat_model_by_provider)
        self.assertIsNot(wrappers[0], wrappers[1])
        self.assertIsNot(wrappers[0].primary, wrappers[1].primary)


class EmbeddingCrossLoopTests(unittest.TestCase):
    """embedding 两层实例缓存消除（同一不变量扩展）：
    _create_single_embedding 的 _embedding_instance_cache LRU（层 A）与
    get_embeddings_with_fallback 的 _embedding_fallback_cache dict（层 B）。"""

    def setUp(self):
        self._provider_cfg = {
            "id": "openai",
            "label": "OpenAI",
            "dimension": 1536,
            "factory_path": "fake.factory.path",
            "supported_params": set(),
        }
        patches = [
            mock.patch.object(
                embedding_factory,
                "get_embedding_fallback_chain",
                return_value=[dict(self._provider_cfg)],
            ),
            mock.patch("Django_xm.apps.ai_engine.models.SystemConfig.get_value", return_value={}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_per_provider_embedding_cache_bypassed_across_loops(self):
        """层 A（use_cache=False 跳过层 B）：两次获取（两个 loop）→ 不同实例且 loop B aembed 成功。"""
        results: list[Embeddings] = []

        with mock.patch.object(embedding_factory, "_import_factory") as import_factory:
            import_factory.side_effect = lambda path: (lambda **kwargs: _make_loop_bound_embeddings()[0])

            async def get_and_use():
                emb = embedding_factory.get_embeddings_with_fallback(
                    use_cache=False, use_fallback=False
                )
                results.append(emb)
                await emb.aembed_query("hi")

            asyncio.run(get_and_use())  # loop A：获取 + 首用绑定，随 run 关闭
            asyncio.run(get_and_use())  # loop B：再获取 + 使用

        # 旧 LRU 实现：第二次命中层 A 缓存返回同一实例 → loop B aembed 报错
        self.assertIsNot(results[0], results[1])

    def test_fallback_level_embedding_cache_bypassed(self):
        """层 B（use_cache=True，_create_single_embedding 打桩隔离层 A）：
        两次获取 → 不同实例（旧 dict 缓存下第二次命中层 B 返回同实例）。"""
        results: list[Embeddings] = []

        with mock.patch.object(embedding_factory, "_create_single_embedding") as create_emb:
            create_emb.side_effect = lambda cfg, **kwargs: _make_loop_bound_embeddings()[0]

            for _ in range(2):
                results.append(
                    embedding_factory.get_embeddings_with_fallback(
                        use_cache=True, use_fallback=False
                    )
                )

        self.assertIsNot(results[0], results[1])


class NoInstanceCacheResidueTests(unittest.TestCase):
    """缓存点清零反断言（spec Scenario：缓存点清零）。"""

    def test_no_module_level_model_instance_caches(self):
        self.assertFalse(hasattr(llm_factory, "_helper_model_cache"))
        self.assertFalse(hasattr(llm_factory, "_model_instance_cache"))

        from Django_xm.apps.ai_engine.services import llm_cache

        self.assertFalse(hasattr(llm_cache, "cached_model_creation"))
        self.assertFalse(hasattr(embedding_factory, "_embedding_fallback_cache"))
        self.assertFalse(hasattr(embedding_factory, "_embedding_instance_cache"))
        self.assertFalse(hasattr(embedding_factory, "reset_embedding_factory"))

        # model_cache 模块整体退役
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("Django_xm.apps.ai_engine.services.model_cache")


if __name__ == "__main__":
    unittest.main()
