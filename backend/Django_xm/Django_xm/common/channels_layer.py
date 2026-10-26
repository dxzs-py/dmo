"""channels_redis 通道层健壮封装。

背景（真实故障）：channel layer 曾配置 ``symmetric_encryption_keys=[SECRET_KEY]``，
跨进程事件由发送方加密、接收方解密。当 Django(8000) 与 FastAPI(8001) 进程的
SECRET_KEY 不一致时（例如某个进程由 IDE/终端带入了旧环境变量），接收端在
``channels_redis.core.RedisChannelLayer.receive_single`` 里直接 deserialize，
抛 ``cryptography.fernet.InvalidToken`` 且无任何兜底，异常会打断整个 consumer
协程 → WebSocket 以 1011 关闭且不触发 ``disconnect()``：

- 前端只看到“连接每秒断一次、重连风暴”；
- 生成期间每 500ms 的正文快照都会再打死一次新连接；
- 生成结束后不再有事件，连接才活下来并一次性回放全部历史 → “最后才看到结果”。

本模块提供 ``ResilientRedisChannelLayer``：单条解码失败的消息只记录 ERROR 并丢弃
（消息已经从队列弹出，不会毒化后续接收），不再打断连接；Redis 连接类错误照常抛出，
交由 channels 正常处理，避免掩盖真实的故障。
"""

from __future__ import annotations

import asyncio
import logging

from channels_redis.core import RedisChannelLayer

logger = logging.getLogger(__name__)

# Redis 连接/超时类错误必须上抛（不能当作坏消息吞掉，否则 Redis 故障被隐藏）
try:  # pragma: no cover - 依赖环境
    from redis.exceptions import RedisError as _RedisError

    _REDIS_ERRORS: tuple[type[BaseException], ...] = (_RedisError,)
except Exception:  # pragma: no cover - redis 未安装
    _REDIS_ERRORS = ()


class ResilientRedisChannelLayer(RedisChannelLayer):
    """对单条不可解码消息免疫的 Redis 通道层。

    覆盖 ``receive``：捕获解密/反序列化异常 → 记录 ERROR → 短暂让出事件循环后继续
    接收下一条，保证一条坏消息不会踢掉整条 WebSocket 连接。
    """

    async def receive(self, channel):
        while True:
            try:
                return await super().receive(channel)
            except Exception as exc:
                # Redis 连接/超时类错误不吞：那是真故障，交给 channels 处理
                if _REDIS_ERRORS and isinstance(exc, _REDIS_ERRORS):
                    raise
                logger.exception(
                    "[ChannelLayer] 丢弃无法解码的通道消息（连接保持存活）: "
                    "channel=%s, error=%s",
                    channel,
                    type(exc).__name__,
                )
                # 让出事件循环，避免同一批坏消息造成忙等
                await asyncio.sleep(0.05)
