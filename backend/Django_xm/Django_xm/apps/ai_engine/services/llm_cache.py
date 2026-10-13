"""LLM 速率限制基础设施

从 llm_factory.py 拆分而来，职责：
1. 速率限制器（InMemoryRateLimiter）单例

历史沿革（eliminate-priority-debts 阶段 1）：
- 模型实例缓存（cached_model_creation + model_cache.py 的 LRU）已退役：
  多 loop 架构下 async 模型实例跨事件循环复用不安全，实例构建即时化
  （不变量：async 模型实例及其异步客户端永不跨 loop 复用）。
- 全局 LLM 请求缓存轨道（原 langchain 全局缓存接线入口）已下线，
  模型响应缓存由 cache_manager.ModelResponseCacheService 唯一承担。

依赖关系：
- 本模块是底层基础设施，不依赖 llm_factory / llm_fallback

参考：
- https://reference.langchain.com/python/langchain/rate_limiters
"""

from __future__ import annotations

import threading
from typing import Any

from Django_xm.apps.core.logging_utils import get_logger

from ..config import settings

logger = get_logger(__name__)


# ============== 速率限制器（按 provider 独立计数） ==============

# 多 Agent 并行（主 agent + N 子代理 + 辅助模型）共享 LLM 出口：
# 限速按 provider 独立计数（groq 一个桶、deepseek 一个桶……本地 provider 不限速），
# 避免"全局 1 req/s 单桶"让并行子代理互相排队。
_rate_limiter_lock = threading.Lock()
_rate_limiters: dict[str, Any | None] = {}


def get_rate_limiter(provider_id: str | None = None) -> Any:
    """获取指定 provider 的 InMemoryRateLimiter（进程内按 provider 缓存）。

    Args:
        provider_id: provider 标识（如 "groq" / "deepseek" / "ollama"）。
            属于 rate_limit_unlimited_providers（默认含 ollama）→ 返回 None（不限速）；
            命中 rate_limit_rps_overrides → 按 override 速率；
            其余外部 API → rate_limit_rps 默认档。

    Returns:
        InMemoryRateLimiter 实例；不限速或依赖不可用时返回 None（调用方据此跳过接线）。

    loop 安全性：InMemoryRateLimiter 仅使用 threading.Lock + time.monotonic
    令牌桶（aacquire 内的 asyncio.sleep 为调用局部等待，无共享 asyncio
    原语），跨事件循环复用安全，故按 provider 保留进程级实例。
    """
    key = provider_id or "_default_"
    with _rate_limiter_lock:
        if key in _rate_limiters:
            return _rate_limiters[key]

        try:
            from langchain_core.rate_limiters import InMemoryRateLimiter

            unlimited = bool(provider_id) and provider_id in settings.rate_limit_unlimited_providers
            override = settings.rate_limit_rps_overrides.get(provider_id) if provider_id else None
            if unlimited:
                rps = None
            elif override is not None and override > 0:
                rps = float(override)
            else:
                rps = float(settings.rate_limit_rps)

            if rps is None:
                limiter = None
                logger.info(f"provider={provider_id} 为本地/不限速 provider，跳过速率限制")
            else:
                limiter = InMemoryRateLimiter(
                    requests_per_second=rps,
                    check_every_n_seconds=0.1,
                    max_bucket_size=settings.rate_limit_bucket,
                )
                logger.info(
                    f"速率限制器已创建: provider={provider_id}, {rps} req/s, "
                    f"bucket={settings.rate_limit_bucket}"
                )
            _rate_limiters[key] = limiter
            return limiter
        except ImportError:
            logger.warning("langchain_core.rate_limiters.InMemoryRateLimiter 不可用，将不使用速率限制")
            _rate_limiters[key] = None
            return None
        except Exception as e:
            logger.warning(f"provider={provider_id} 速率限制器创建失败: {e}，将不使用速率限制")
            _rate_limiters[key] = None
            return None
