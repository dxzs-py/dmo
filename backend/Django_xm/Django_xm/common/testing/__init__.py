"""测试工厂统一出口（ARCH-20 测试保护网）。

Mock 替身统一从这里导入：

    from Django_xm.common.testing import create_fake_llm, create_fake_redis

禁止在测试文件内再复制本地替身类（_FakeModel/_FakeLLM/_FakeChunk/_FakeRedis/
FakeAgent 等），存量测试按批次迁移。
"""

from Django_xm.common.testing.factories import (
    FakeAgent,
    FakeChatModel,
    FakeChunk,
    FakeLLM,
    FakeRedis,
    create_fake_agent,
    create_fake_chat_model,
    create_fake_chunk,
    create_fake_llm,
    create_fake_redis,
    create_fake_stream_chunks,
    create_generic_fake_chat_model,
)

__all__ = [
    "FakeAgent",
    "FakeChatModel",
    "FakeChunk",
    "FakeLLM",
    "FakeRedis",
    "create_fake_agent",
    "create_fake_chat_model",
    "create_fake_chunk",
    "create_fake_llm",
    "create_fake_redis",
    "create_fake_stream_chunks",
    "create_generic_fake_chat_model",
]
