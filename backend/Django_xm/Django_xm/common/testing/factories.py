"""统一 Mock 测试设施工厂（ARCH-20 测试保护网）。

背景：此前各测试文件分别自定义 _FakeModel / _FakeLLM / _FakeChunk /
_FakeRedis / FakeAgent / GenericFakeChatModel，语义相近但实现分散、无从复用。
本模块将其提炼为唯一共享工厂；新增测试一律从这里取用替身，禁止再复制本地版本。

设计原则：
- 零业务依赖：仅依赖 langchain_core 与标准库，不触碰 Django 配置，
  纯单元测试在 pytest / manage.py test 双轨下均可直接导入；
- 语义兼容：替身的属性名与方法签名与历史本地版本逐字一致
  （如 FakeRedis._store/_meta、FakeLLM.invoke_messages），存量测试可无痛迁移；
- 工厂函数以 create_ 前缀命名，每次返回全新实例，禁止跨测试共享单例状态。

用法示例：
    from Django_xm.common.testing import create_fake_llm, create_fake_chat_model

    # 三协议（invoke/stream/astream）替身：记录收到的 messages，分片输出
    fake_llm = create_fake_llm(pieces=("回答甲", "回答乙"))
    chunk = fake_llm.invoke([("human", "hi")])
    assert chunk.content == "回答甲回答乙"

    # langchain BaseChatModel 替身：返回 (model, counter)，供 LazyFallbackChatModel 等使用
    model, counter = create_fake_chat_model(responses=["fallback-response"])
    model, counter = create_fake_chat_model(error=SomeException("boom"))
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Iterator
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

# FakeLLM 默认分片输出：与 strict_rag 收敛测试的原本地实现保持一致，
# 便于复用 "回答甲回答乙" 拼接语义的既有断言。
_DEFAULT_LLM_PIECES = ("回答甲", "回答乙")

# create_fake_chat_model 默认响应内容（单响应即可覆盖大多数调用计数场景）
_DEFAULT_MODEL_RESPONSE = "ok"


class FakeChunk:
    """最小流式响应块替身：仅携带 content 属性（strict_rag 管道消费的最小接口）。"""

    def __init__(self, content: str) -> None:
        self.content = content


class FakeLLM:
    """invoke / stream / astream 三协议替身，记录收到的 messages。

    - pieces：流式分片列表；invoke 返回其拼接结果，stream/astream 逐片产出；
    - invoke_messages / stream_messages / astream_messages：记录三种协议
      各自最近一次收到的 messages，供断言 prompt 注入内容。
    """

    def __init__(self, pieces: Iterable[str] = _DEFAULT_LLM_PIECES) -> None:
        self.pieces = list(pieces)
        self.invoke_messages: Any = None
        self.stream_messages: Any = None
        self.astream_messages: Any = None

    def invoke(self, messages: Any) -> FakeChunk:
        self.invoke_messages = messages
        return FakeChunk("".join(self.pieces))

    def stream(self, messages: Any) -> Iterator[FakeChunk]:
        self.stream_messages = messages
        for piece in self.pieces:
            yield FakeChunk(piece)

    async def astream(self, messages: Any) -> AsyncIterator[FakeChunk]:
        self.astream_messages = messages
        for piece in self.pieces:
            yield FakeChunk(piece)


class FakeChatModel(BaseChatModel):
    """langchain BaseChatModel 替身基类（_generate 行为由工厂闭包子类定制）。

    pydantic 模型禁止任意实例属性，故调用计数由外部 counter dict 承载，
    经工厂函数返回给调用方断言。
    """

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None, **kwargs: Any) -> ChatResult:
        raise NotImplementedError("行为由 create_fake_chat_model 闭包子类定制")


def create_fake_chat_model(
    responses: Iterable[Any] | None = None,
    error: Exception | None = None,
) -> tuple[FakeChatModel, dict[str, int]]:
    """构建 BaseChatModel 替身，返回 (模型实例, 调用计数器)。

    Args:
        responses: 响应序列，元素为 str（自动包装 AIMessage）或 BaseMessage；
            依次返回，耗尽后重复最后一条。error 提供时忽略本参数。
        error: 非空时每次 _generate 计数后抛出该异常（降级/熔断场景）。

    Returns:
        (model, counter)。counter 为 {"generate_calls": int} 闭包可变容器，
        规避 pydantic 模型禁止任意实例属性的限制，与历史本地实现一致。
    """
    counter = {"generate_calls": 0}
    reply_messages: list[BaseMessage] = [
        AIMessage(content=r) if isinstance(r, str) else r for r in (responses or [_DEFAULT_MODEL_RESPONSE])
    ]
    error_exc = error

    class _BehaviorModel(FakeChatModel):
        def _generate(self, messages, stop=None, **kwargs):
            counter["generate_calls"] += 1
            if error_exc is not None:
                raise error_exc
            # 依次返回，耗尽后重复最后一条（单响应时等价于历史"恒定返回"语义）
            index = min(counter["generate_calls"] - 1, len(reply_messages) - 1)
            return ChatResult(generations=[ChatGeneration(message=reply_messages[index])])

    return _BehaviorModel(), counter


class FakeRedis:
    """进程内异步 Redis 客户端替身（get / set(ex, nx) / delete）。

    - fail=True：所有操作抛 ConnectionError("redis down")，模拟 Redis 宕机
      自动降级路径（与历史本地版本逐字一致）；
    - _store：key → bytes 存储；_meta：key → 附加元数据（如 ex 过期时间）。
      两个属性名与历史本地版本一致，存量断言（redis._store 等）无需修改。
    """

    def __init__(self, fail: bool = False) -> None:
        self._store: dict[str, bytes] = {}
        self._meta: dict[str, dict] = {}
        self._fail = fail

    async def get(self, key: str) -> bytes | None:
        if self._fail:
            raise ConnectionError("redis down")
        return self._store.get(key)

    async def set(self, key: str, value: bytes, ex: int | None = None, nx: bool = False) -> bool:
        if self._fail:
            raise ConnectionError("redis down")
        if nx and key in self._store:
            return False
        self._store[key] = value
        self._meta[key] = {"ex": ex}
        return True

    async def delete(self, key: str) -> bool:
        if self._fail:
            raise ConnectionError("redis down")
        return self._store.pop(key, None) is not None


class FakeAgent:
    """深度研究 agent 替身：astream_research_with_interrupts 捕获 config 后返回固定结果。

    captured_config 记录最近一次调用的 config，供断言 configurable 注入项
    （thread_id / enable_sandbox 等）。
    """

    def __init__(self, final_result: dict | None = None) -> None:
        self.captured_config: dict | None = None
        self._final_result = final_result or {"success": True, "final_report": "报告"}

    async def astream_research_with_interrupts(
        self,
        query: str,
        config: dict,
        callbacks: Any = None,
        on_interrupt: Any = None,
        resume_command: Any = None,
    ) -> dict:
        self.captured_config = config
        return self._final_result


def create_fake_llm(pieces: Iterable[str] = _DEFAULT_LLM_PIECES) -> FakeLLM:
    """构建三协议（invoke/stream/astream）LLM 替身，默认分片 ("回答甲", "回答乙")。"""
    return FakeLLM(pieces=pieces)


def create_fake_chunk(content: str) -> FakeChunk:
    """构建单个流式响应块替身。"""
    return FakeChunk(content)


def create_fake_stream_chunks(contents: Iterable[str]) -> list[FakeChunk]:
    """构建一批流式响应块替身（按 contents 顺序排列）。"""
    return [FakeChunk(content) for content in contents]


def create_fake_redis(fail: bool = False) -> FakeRedis:
    """构建进程内异步 Redis 替身；fail=True 时所有操作模拟宕机抛错。"""
    return FakeRedis(fail=fail)


def create_fake_agent(final_result: dict | None = None) -> FakeAgent:
    """构建深度研究 agent 替身，astream_research_with_interrupts 捕获 config。"""
    return FakeAgent(final_result=final_result)


def create_generic_fake_chat_model(replies: Iterable[Any]) -> GenericFakeChatModel:
    """构建 langchain_core GenericFakeChatModel（create_agent 集成测试用）。

    replies：str 序列（自动包装为 AIMessage）或 BaseMessage 序列，循环产出；
    与历史用法 GenericFakeChatModel(messages=iter([...])) 语义一致。
    """
    messages = iter(AIMessage(content=r) if isinstance(r, str) else r for r in replies)
    return GenericFakeChatModel(messages=messages)
