"""LLM Fallback 机制

从 llm_factory.py 拆分而来，职责：
1. 运行时模型降级（LazyFallbackChatModel）：主模型失败时自动切换到候选模型
2. 结构化输出 fallback（StructuredModelWithFallback）：with_structured_output 不兼容 with_fallbacks 的手动实现
3. Fallback 检测回调（FallbackDetectionCallback）：检测 LangChain with_fallbacks 的运行时切换
4. 候选列表生成（get_fallback_candidates）：按优先级解析可用的 fallback 模型

依赖关系（DAG，无循环）：
- llm_fallback → llm_cache（不直接依赖，通过 factory 闭包间接使用缓存）
- llm_factory → llm_fallback（公开 API 使用 fallback 类）

设计要点：
- LazyFallbackChatModel 通过 `factory` 参数接收模型创建函数（依赖注入），
  避免 llm_fallback → llm_factory 的循环导入
- StructuredModelWithFallback 同样通过 `factory` 参数接收创建函数
- get_fallback_candidates 依赖 SystemConfig + HELPER_MODEL_PRIORITY + registry_service，
  不依赖 llm_factory

参考：
- https://docs.langchain.com/oss/python/langchain/fallbacks
- Claude Code Task 工具的子 agent 设计（主 agent 处理审批，子 agent 专注只读研究）
"""
from __future__ import annotations

import time
import warnings
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_core.runnables import RunnableConfig

from Django_xm.apps.core.config import get_logger

from ..config import HELPER_MODEL_PRIORITY, settings
from .registry_service import (
    get_model_registry,
)
from .registry_service import (
    is_provider_available as registry_is_provider_available,
)

logger = get_logger(__name__)


# ============== Pydantic 序列化警告上下文管理器 ==============
# 替代原 llm_factory.py 顶部的全局 warnings.filterwarnings
#
# 触发场景：langchain_openai 在解析 OpenAI structured output 响应时，会将
# Pydantic BaseModel 实例存入 ChatGeneration.message.additional_kwargs["parsed"]。
# LangChain tracer 在 on_llm_end 回调中调用 LLMResult.model_dump() 时，
# Pydantic v2 发现该字段类型推断为 None 但实际是 BaseModel 实例，
# 触发 PydanticSerializationUnexpectedValue 警告。
#
# 处理策略：仅在结构化输出 invoke 调用期间局部抑制该特定警告，
# 而非全局抑制（避免遮蔽其他真实警告）。

_PYDANTIC_SERIALIZATION_WARNING_PATTERN = (
    r"Pydantic serializer warnings[\s\S]*PydanticSerializationUnexpectedValue"
)


@contextmanager
def suppress_pydantic_serialization_warning():
    """局部抑制 Pydantic + OpenAI SDK 结构化输出的序列化警告

    用法：
        with suppress_pydantic_serialization_warning():
            result = structured.invoke(input, config=config)
    """
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=_PYDANTIC_SERIALIZATION_WARNING_PATTERN,
            category=UserWarning,
        )
        yield


# ============== 连接错误判定 ==============

def is_connection_error(exc: Exception) -> bool:
    """判断异常是否为可触发 fallback 的连接/认证类错误

    仅对以下错误触发 fallback，其他错误（如参数错误）不触发：
    - 401/403 认证/权限错误（余额不足、Key 无效等）
    - 429 速率限制
    - 连接超时 / 网络不可达
    - 502/503 服务不可用
    """
    error_type = type(exc).__name__
    error_module = type(exc).__module__

    # OpenAI SDK 错误
    if "openai" in error_module:
        if error_type in (
            "AuthenticationError",
            "PermissionDeniedError",
            "RateLimitError",
            "APIConnectionError",
            "APITimeoutError",
        ):
            return True

    # Anthropic SDK 错误
    if "anthropic" in error_module:
        if error_type in (
            "AuthenticationError",
            "PermissionDeniedError",
            "RateLimitError",
            "APIConnectionError",
            "APITimeoutError",
        ):
            return True

    # 通用网络错误
    if error_type in (
        "ConnectionError",
        "TimeoutError",
        "ConnectTimeoutError",
        "SSLError",
    ):
        return True

    # httpx / urllib3 连接错误
    if "ConnectTimeout" in error_type or "ConnectionError" in error_type:
        return True

    # HTTP 状态码判断
    status_code = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    if status_code in (401, 403, 429, 502, 503):
        return True

    return False


# ============== Fallback 候选列表 ==============

def get_fallback_candidates(
    exclude_provider: str | None = None,
    exclude_model: str | None = None,
) -> list[tuple[str, str]]:
    """获取可用的 fallback 模型候选列表

    优先级（从高到低）：
    1. **用户配置的降级模型**（SystemConfig.fallback_chat_model）
       - 用户在 Admin 设置的"降级模型"
       - 仅当 provider 可用（API Key 已配置）时纳入
    2. **HELPER_MODEL_PRIORITY 中第一个可用的**（兜底）
       - 从配置的辅助模型优先级列表中取第一个可用项
       - 避免自动拉取所有 HELPER_MODEL_PRIORITY 和 MODEL_REGISTRY 中的模型，
         防止创建用户未配置的模型实例

    Args:
        exclude_provider: 排除的 provider id（通常是主模型的 provider，避免重复）
        exclude_model: 排除的模型名（通常是主模型，避免重复）

    Returns:
        [(provider_id, model_name), ...] 候选列表，按优先级排序
    """
    candidates: list[tuple[str, str]] = []

    # 1. 最高优先级：用户配置的降级模型
    try:
        from Django_xm.apps.ai_engine.models import SystemConfig
        fb_config = SystemConfig.get_value("fallback_chat_model", {})
        fb_provider = fb_config.get("provider_id", "")
        fb_model = fb_config.get("model_name", "")
        if fb_provider and fb_model:
            if not (fb_provider == exclude_provider and fb_model == exclude_model):
                if registry_is_provider_available(fb_provider):
                    candidates.append((fb_provider, fb_model))
    except Exception:
        pass

    # 2. 兜底：若用户未配置降级模型，从 HELPER_MODEL_PRIORITY 中取第一个可用的
    if not candidates:
        for item in HELPER_MODEL_PRIORITY:
            pid = item["provider"]
            mname = item["model"]
            if pid == exclude_provider and mname == exclude_model:
                continue
            if registry_is_provider_available(pid):
                candidates.append((pid, mname))
                break  # 只取一个兜底

    return candidates


# ============== 懒加载 Fallback 模型包装 ==============

class LazyFallbackChatModel(BaseChatModel):
    """懒加载 Fallback 模型包装

    不预实例化 fallback 模型，仅在主模型实际失败时按需创建。
    代理主模型的所有方法，失败时自动切换到候选模型。

    兼容性：
    - 提供 .bound 属性（等同于 .primary），兼容旧代码 getattr(model, 'bound', model)
    - 内置 fallback 状态追踪，通过 fallback_detected / actual_provider / actual_model 属性
      替代 FallbackDetectionCallback 的回调检测机制

    Circuit Breaker 状态机：
    - closed: 正常调用主模型
    - open: 主模型连续失败 ≥2 次，冷却期间直接使用 fallback
    - half_open: 冷却期满后试探主模型，成功则回 closed，失败则回 open

    Args:
        primary: 主模型实例
        fallback_candidates: 候选列表 [(provider_id, model_name), ...]
        factory: 创建模型的工厂函数（依赖注入，避免循环导入 llm_factory）
        temperature: 传递给工厂的温度参数
        max_tokens: 传递给工厂的最大 token 参数
        streaming: 传递给工厂的流式参数
    """

    primary: BaseChatModel
    fallback_candidates: list[tuple[str, str]]
    factory: Callable
    temperature: float | None = None
    max_tokens: int | None = None
    streaming: bool | None = None
    circuit_breaker_cooldown: float = 30.0

    class Config:
        arbitrary_types_allowed = True

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self._fallback_detected: bool = False
        self._actual_provider: str | None = None
        self._actual_model: str | None = None
        # Fallback 模型缓存（同一对话内复用）
        self._active_fallback_model: BaseChatModel | None = None
        self._fallback_provider_id: str | None = None
        self._fallback_model_name: str | None = None
        # Circuit Breaker 状态
        self._circuit_state: str = "closed"  # closed / open / half_open
        self._circuit_opened_at: float = 0.0
        self._consecutive_failures: int = 0

    @property
    def _llm_type(self) -> str:
        return f"lazy-fallback({self.primary._llm_type})"

    @property
    def bound(self) -> BaseChatModel:
        """兼容 RunnableWithFallbacks.bound 属性，返回底层主模型"""
        return self.primary

    @property
    def fallback_detected(self) -> bool:
        """是否发生了 fallback（与 FallbackDetectionCallback 接口一致）"""
        return self._fallback_detected

    @property
    def actual_provider(self) -> str | None:
        """实际使用的模型 provider（与 FallbackDetectionCallback 接口一致）"""
        return self._actual_provider

    @property
    def actual_model(self) -> str | None:
        """实际使用的模型名称（与 FallbackDetectionCallback 接口一致）"""
        return self._actual_model

    def get_fallback_info(self) -> dict[str, str] | None:
        """如果检测到降级，返回降级信息；否则返回 None（与 FallbackDetectionCallback 接口一致）"""
        if not self._fallback_detected:
            return None
        return {
            "original_provider": getattr(self.primary, '_provider_id', '') or '',
            "original_model": getattr(self.primary, 'model_name', '') or getattr(self.primary, 'model', '') or '',
            "actual_provider": self._actual_provider or '',
            "actual_model": self._actual_model or '',
            "message": (
                f"模型 {getattr(self.primary, '_provider_id', '')}/{getattr(self.primary, 'model', '')} 运行时失败，"
                f"已自动切换到 {self._actual_provider}/{self._actual_model}"
            ),
        }

    def _mark_fallback(self, provider_id: str, model_name: str) -> None:
        """记录 fallback 发生"""
        self._fallback_detected = True
        self._actual_provider = provider_id
        self._actual_model = model_name
        logger.info(
            f"LazyFallback 降级: {getattr(self.primary, '_provider_id', '')}/{getattr(self.primary, 'model', '')} "
            f"-> {provider_id}/{model_name}"
        )

    def _is_permanent_error(self, error: Exception) -> bool:
        """判断是否为永久性错误（不可恢复，不应重试）"""
        try:
            from Django_xm.apps.ai_engine.services.exceptions import classify_exception
            classified = classify_exception(error)
            return not classified.recoverable
        except Exception:
            # fallback 到字符串匹配（避免循环导入等异常情况）
            error_str = str(error).lower()
            return any(kw in error_str for kw in ("401", "403", "invalid_credentials", "authentication", "unauthorized"))

    def _is_input_error(self, error: Exception) -> bool:
        """判断是否为输入错误（不应降级到 fallback，应直接抛出）"""
        try:
            from Django_xm.apps.ai_engine.services.exceptions import classify_exception
            classified = classify_exception(error)
            return classified.error_code in ("GUARDRAILS_VALIDATION_ERROR",)
        except Exception:
            # fallback 到字符串匹配（避免循环导入等异常情况）
            error_str = str(error).lower()
            return any(kw in error_str for kw in ("400", "invalid_request"))

    def _should_try_primary(self) -> bool:
        """判断是否应该尝试主模型（Circuit Breaker 逻辑）"""
        if self._circuit_state == "closed":
            return True
        if self._circuit_state == "open":
            if time.monotonic() - self._circuit_opened_at >= self.circuit_breaker_cooldown:
                self._circuit_state = "half_open"
                logger.info("Circuit Breaker: OPEN -> HALF_OPEN，试探主模型")
                return True
            return False
        if self._circuit_state == "half_open":
            return True
        return False

    def _on_primary_success(self):
        """主模型调用成功"""
        if self._circuit_state != "closed":
            logger.info("Circuit Breaker: -> CLOSED，主模型恢复")
        self._circuit_state = "closed"
        self._consecutive_failures = 0

    def _on_primary_failure(self, error: Exception):
        """主模型调用失败"""
        self._consecutive_failures += 1
        if self._is_permanent_error(error) or self._consecutive_failures >= 2:
            self._circuit_state = "open"
            self._circuit_opened_at = time.monotonic()
            logger.info(
                f"Circuit Breaker: -> OPEN（连续失败 {self._consecutive_failures} 次，"
                f"冷却 {self.circuit_breaker_cooldown}s）"
            )
        elif self._circuit_state == "half_open":
            self._circuit_state = "open"
            self._circuit_opened_at = time.monotonic()
            logger.info("Circuit Breaker: HALF_OPEN -> OPEN，主模型仍不可用")

    def _get_or_create_fallback(self, provider_id: str, model_name: str) -> BaseChatModel | None:
        """获取或创建 fallback 模型（带缓存，同一对话内复用）"""
        if (self._active_fallback_model is not None
            and self._fallback_provider_id == provider_id
            and self._fallback_model_name == model_name):
            return self._active_fallback_model
        fb_model = self._resolve_fallback_model(provider_id, model_name)
        if fb_model is not None:
            self._active_fallback_model = fb_model
            self._fallback_provider_id = provider_id
            self._fallback_model_name = model_name
        return fb_model

    def _resolve_fallback_model(self, provider_id: str, model_name: str) -> BaseChatModel | None:
        """按需创建 fallback 模型实例（通过注入的 factory）"""
        try:
            model = self.factory(
                provider_id=provider_id,
                model_name=model_name,
                temperature=self.temperature if self.temperature is not None else settings.openai_temperature,
                max_tokens=self.max_tokens,
                streaming=self.streaming if self.streaming is not None else settings.openai_streaming,
                max_retries=0,
            )
            logger.info(f"懒加载 Fallback 模型已创建: {provider_id}/{model_name}")
            return model
        except Exception as e:
            logger.warning(f"懒加载 Fallback 模型创建失败 {provider_id}/{model_name}: {e}")
            return None

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None, **kwargs: Any) -> ChatResult:
        # Circuit Breaker: 主模型处于 OPEN 状态时直接使用 fallback
        if not self._should_try_primary():
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    return fb_model._generate(messages, stop=stop, **kwargs)
            raise RuntimeError("主模型不可用且无可用 fallback 模型")

        try:
            result = self.primary._generate(messages, stop=stop, **kwargs)
            self._on_primary_success()
            return result
        except Exception as primary_error:
            if self._is_input_error(primary_error):
                raise primary_error
            self._on_primary_failure(primary_error)
            logger.warning(f"主模型生成失败，尝试 fallback: {primary_error}")
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    try:
                        self._mark_fallback(pid, mname)
                        return fb_model._generate(messages, stop=stop, **kwargs)
                    except Exception as fb_error:
                        self._fallback_detected = False
                        logger.warning(f"Fallback 模型 {pid}/{mname} 也失败: {fb_error}")
                        continue
            raise primary_error

    async def _agenerate(self, messages: list[BaseMessage], stop: list[str] | None = None, **kwargs: Any) -> ChatResult:
        # Circuit Breaker: 主模型处于 OPEN 状态时直接使用 fallback
        if not self._should_try_primary():
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    return await fb_model._agenerate(messages, stop=stop, **kwargs)
            raise RuntimeError("主模型不可用且无可用 fallback 模型")

        try:
            result = await self.primary._agenerate(messages, stop=stop, **kwargs)
            self._on_primary_success()
            return result
        except Exception as primary_error:
            if self._is_input_error(primary_error):
                raise primary_error
            self._on_primary_failure(primary_error)
            logger.warning(f"主模型异步生成失败，尝试 fallback: {primary_error}")
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    try:
                        self._mark_fallback(pid, mname)
                        return await fb_model._agenerate(messages, stop=stop, **kwargs)
                    except Exception as fb_error:
                        self._fallback_detected = False
                        logger.warning(f"Fallback 模型 {pid}/{mname} 异步也失败: {fb_error}")
                        continue
            raise primary_error

    def _stream(self, messages: list[BaseMessage], stop: list[str] | None = None, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        # Circuit Breaker: 主模型处于 OPEN 状态时直接使用 fallback
        if not self._should_try_primary():
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    yield from fb_model._stream(messages, stop=stop, **kwargs)
                    return
            raise RuntimeError("主模型不可用且无可用 fallback 模型")

        try:
            chunks = 0
            for chunk in self.primary._stream(messages, stop=stop, **kwargs):
                chunks += 1
                yield chunk
            self._on_primary_success()
            if chunks:
                logger.debug(f"主模型流式返回 {chunks} 个 chunk")
        except Exception as primary_error:
            if self._is_input_error(primary_error):
                raise primary_error
            self._on_primary_failure(primary_error)
            logger.warning(f"主模型流式失败，尝试 fallback: {primary_error}")
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    try:
                        self._mark_fallback(pid, mname)
                        chunks = 0
                        for chunk in fb_model._stream(messages, stop=stop, **kwargs):
                            chunks += 1
                            yield chunk
                        logger.info(f"Fallback 流式返回 {chunks} 个 chunk: {pid}/{mname}")
                        return
                    except Exception as fb_error:
                        self._fallback_detected = False
                        logger.warning(f"Fallback 模型 {pid}/{mname} 流式也失败: {fb_error}")
                        continue
            raise primary_error

    async def _astream(self, messages: list[BaseMessage], stop: list[str] | None = None, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        # Circuit Breaker: 主模型处于 OPEN 状态时直接使用 fallback
        if not self._should_try_primary():
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    async for chunk in fb_model._astream(messages, stop=stop, **kwargs):
                        yield chunk
                    return
            raise RuntimeError("主模型不可用且无可用 fallback 模型")

        try:
            chunks = 0
            async for chunk in self.primary._astream(messages, stop=stop, **kwargs):
                chunks += 1
                yield chunk
            self._on_primary_success()
            if chunks:
                logger.debug(f"主模型异步流式返回 {chunks} 个 chunk")
        except Exception as primary_error:
            if self._is_input_error(primary_error):
                raise primary_error
            self._on_primary_failure(primary_error)
            logger.warning(f"主模型异步流式失败，尝试 fallback: {primary_error}")
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    try:
                        self._mark_fallback(pid, mname)
                        chunks = 0
                        async for chunk in fb_model._astream(messages, stop=stop, **kwargs):
                            chunks += 1
                            yield chunk
                        logger.info(f"Fallback 异步流式返回 {chunks} 个 chunk: {pid}/{mname}")
                        return
                    except Exception as fb_error:
                        self._fallback_detected = False
                        logger.warning(f"Fallback 模型 {pid}/{mname} 异步流式也失败: {fb_error}")
                        continue
            raise primary_error

    def invoke(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        # Circuit Breaker: 主模型处于 OPEN 状态时直接使用 fallback
        if not self._should_try_primary():
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    return fb_model.invoke(input, config=config, **kwargs)
            raise RuntimeError("主模型不可用且无可用 fallback 模型")

        try:
            result = self.primary.invoke(input, config=config, **kwargs)
            self._on_primary_success()
            return result
        except Exception as primary_error:
            if self._is_input_error(primary_error):
                raise primary_error
            self._on_primary_failure(primary_error)
            logger.warning(f"主模型 invoke 失败，尝试 fallback: {primary_error}")
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    try:
                        self._mark_fallback(pid, mname)
                        return fb_model.invoke(input, config=config, **kwargs)
                    except Exception as fb_error:
                        self._fallback_detected = False
                        logger.warning(f"Fallback 模型 {pid}/{mname} invoke 也失败: {fb_error}")
                        continue
            raise primary_error

    async def ainvoke(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        # Circuit Breaker: 主模型处于 OPEN 状态时直接使用 fallback
        if not self._should_try_primary():
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    return await fb_model.ainvoke(input, config=config, **kwargs)
            raise RuntimeError("主模型不可用且无可用 fallback 模型")

        try:
            result = await self.primary.ainvoke(input, config=config, **kwargs)
            self._on_primary_success()
            return result
        except Exception as primary_error:
            if self._is_input_error(primary_error):
                raise primary_error
            self._on_primary_failure(primary_error)
            logger.warning(f"主模型 ainvoke 失败，尝试 fallback: {primary_error}")
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    try:
                        self._mark_fallback(pid, mname)
                        return await fb_model.ainvoke(input, config=config, **kwargs)
                    except Exception as fb_error:
                        self._fallback_detected = False
                        logger.warning(f"Fallback 模型 {pid}/{mname} ainvoke 也失败: {fb_error}")
                        continue
            raise primary_error

    def stream(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Iterator[Any]:
        # Circuit Breaker: 主模型处于 OPEN 状态时直接使用 fallback
        if not self._should_try_primary():
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    yield from fb_model.stream(input, config=config, **kwargs)
                    return
            raise RuntimeError("主模型不可用且无可用 fallback 模型")

        try:
            yield from self.primary.stream(input, config=config, **kwargs)
            self._on_primary_success()
        except Exception as primary_error:
            if self._is_input_error(primary_error):
                raise primary_error
            self._on_primary_failure(primary_error)
            logger.warning(f"主模型 stream 失败，尝试 fallback: {primary_error}")
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    try:
                        self._mark_fallback(pid, mname)
                        yield from fb_model.stream(input, config=config, **kwargs)
                        return
                    except Exception as fb_error:
                        self._fallback_detected = False
                        logger.warning(f"Fallback 模型 {pid}/{mname} stream 也失败: {fb_error}")
                        continue
            raise primary_error

    async def astream(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> AsyncIterator[Any]:
        # Circuit Breaker: 主模型处于 OPEN 状态时直接使用 fallback
        if not self._should_try_primary():
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    async for chunk in fb_model.astream(input, config=config, **kwargs):
                        yield chunk
                    return
            raise RuntimeError("主模型不可用且无可用 fallback 模型")

        try:
            async for chunk in self.primary.astream(input, config=config, **kwargs):
                yield chunk
            self._on_primary_success()
        except Exception as primary_error:
            if self._is_input_error(primary_error):
                raise primary_error
            self._on_primary_failure(primary_error)
            logger.warning(f"主模型 astream 失败，尝试 fallback: {primary_error}")
            for pid, mname in self.fallback_candidates:
                fb_model = self._get_or_create_fallback(pid, mname)
                if fb_model is not None:
                    try:
                        self._mark_fallback(pid, mname)
                        async for chunk in fb_model.astream(input, config=config, **kwargs):
                            yield chunk
                        return
                    except Exception as fb_error:
                        self._fallback_detected = False
                        logger.warning(f"Fallback 模型 {pid}/{mname} astream 也失败: {fb_error}")
                        continue
            raise primary_error

    def bind_tools(self, tools: Any, *, tool_choice=None, **kwargs: Any) -> Any:
        """将 bind_tools 代理到自身，保留 fallback 能力

        修复：原先返回 self.primary.bind_tools()，导致 RunnableBinding
        包装主模型，fallback 机制完全失效。改为 self.bind() 使
        RunnableBinding 包装 LazyFallbackChatModel 自身，_agenerate
        中的 fallback 逻辑得以保留。

        调用链：RunnableBinding.ainvoke() → LazyFallbackChatModel.ainvoke()
        → _agenerate(**kwargs含tools) → primary._agenerate / fallback._agenerate
        """
        from langchain_core.utils.function_calling import convert_to_openai_tool
        formatted_tools = [convert_to_openai_tool(tool) for tool in tools]
        kwargs["tools"] = formatted_tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        return self.bind(**kwargs)

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        """将 with_structured_output 代理到自身，保留 fallback 能力

        与 bind_tools 同理，使用 self.bind() 包装以保留 fallback 逻辑。
        """
        return self.bind(response_format=schema, **kwargs)

    @property
    def _provider_id(self) -> str | None:
        return getattr(self.primary, '_provider_id', None)


# ============== 结构化输出 Fallback（手动实现） ==============

class StructuredModelWithFallback:
    """结构化输出模型 + 手动 fallback（支持懒加载）

    不使用 LangChain 的 with_fallbacks()，因为 with_structured_output
    会包装异常导致 with_fallbacks 无法正确触发 fallback。
    手动遍历模型列表，连接/认证错误时切换下一个模型。
    懒加载候选在运行时按需创建。

    设计：通过 `factory` 参数接收模型创建函数（依赖注入），
    避免 llm_fallback → llm_factory 的循环导入。
    """

    def __init__(
        self,
        structured_models: list[tuple[str, str, Any]],
        creation_errors: list[str],
        lazy_candidates: list[tuple[str, str]] | None = None,
        schema: Any = None,
        factory: Callable | None = None,
    ):
        """
        Args:
            structured_models: 已实例化的结构化模型列表 [(provider, model_name, structured_runnable), ...]
            creation_errors: 主模型创建时的错误信息列表（用于最终错误聚合）
            lazy_candidates: 懒加载候选列表 [(provider_id, model_name), ...]
            schema: Pydantic BaseModel 类，结构化输出的格式
            factory: 模型创建函数，签名为 factory(provider_id, model_name, ...) -> BaseChatModel
        """
        self._models = structured_models
        self._creation_errors = creation_errors
        self._lazy_candidates = lazy_candidates or []
        self._schema = schema
        self._factory = factory

    def _resolve_lazy_candidate(self, provider_id: str, model_name: str) -> tuple[str, str, Any] | None:
        """按需创建懒加载候选的结构化模型（通过注入的 factory）"""
        if self._factory is None:
            logger.warning("StructuredModelWithFallback 未配置 factory，无法创建懒加载候选")
            return None
        try:
            fb_special_params = None
            if provider_id == "deepseek":
                fb_special_params = {"thinking": {"type": "disabled"}}

            fb_model = self._factory(
                provider_id=provider_id,
                model_name=model_name,
                temperature=settings.openai_temperature,
                max_tokens=None,
                streaming=False,
                max_retries=0,
                special_params=fb_special_params,
            )
            fb_structured = fb_model.with_structured_output(self._schema)
            logger.info(f"懒加载 Fallback 结构化模型已创建: {provider_id}/{model_name}")
            return (provider_id, model_name, fb_structured)
        except Exception as e:
            logger.warning(f"懒加载 Fallback 结构化模型创建失败 {provider_id}/{model_name}: {e}")
            return None

    @staticmethod
    def _is_valid_result(result: Any) -> bool:
        """判断结构化输出是否有效

        防御性检查：流式 + with_structured_output 可能返回 None
        或者返回空对象（没有 Pydantic 字段填充）
        """
        if result is None:
            return False
        # Pydantic BaseModel 实例：检查是否有任何字段被填充
        if hasattr(result, "model_dump") and callable(result.model_dump):
            try:
                dumped = result.model_dump(exclude_none=False)
                if not dumped:
                    return False
                # 至少有一个字段包含"实质内容"：
                # - 非 None
                # - 非空字符串
                # - 非空列表/字典
                # - 非零数值
                def _has_meaningful_value(v: Any) -> bool:
                    if v is None:
                        return False
                    if isinstance(v, str):
                        return bool(v.strip())
                    if isinstance(v, (list, dict, tuple, set)):
                        return len(v) > 0
                    if isinstance(v, bool):
                        return v  # True 才算有意义
                    if isinstance(v, (int, float)):
                        return v != 0
                    # 其他类型：非空即可
                    return True

                has_value = any(_has_meaningful_value(v) for v in dumped.values())
                return has_value
            except Exception:
                return True  # 检查失败时保守认为有效
        # 字典/列表
        if isinstance(result, (dict, list)):
            return bool(result)
        # 字符串
        if isinstance(result, str):
            return bool(result.strip())
        return True

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        """调用结构化模型，失败时自动切换（含懒加载候选）

        使用 suppress_pydantic_serialization_warning 上下文管理器局部抑制
        Pydantic v2 + OpenAI SDK 结构化输出的已知兼容性警告
        （原 llm_factory.py 顶部全局 warnings.filterwarnings 的局部化替代）。
        """
        errors: list[str] = list(self._creation_errors)

        for provider, model_name, structured in self._models:
            try:
                with suppress_pydantic_serialization_warning():
                    result = structured.invoke(input, config=config, **kwargs)
                if not self._is_valid_result(result):
                    error_msg = f"{provider}/{model_name}: 返回空结果(None/空对象)"
                    errors.append(error_msg)
                    logger.warning(f"结构化模型返回空结果 {provider}/{model_name}，尝试下一个模型")
                    continue
                logger.info(f"结构化模型调用成功: {provider}/{model_name}")
                return result
            except Exception as e:
                errors.append(f"{provider}/{model_name}: {type(e).__name__}: {e}")
                logger.warning(f"结构化模型调用失败 {provider}/{model_name}: {type(e).__name__}: {e}")
                continue

        # 预实例化模型都失败，尝试懒加载候选
        for pid, mname in self._lazy_candidates:
            lazy_result = self._resolve_lazy_candidate(pid, mname)
            if lazy_result is not None:
                provider, model_name, structured = lazy_result
                try:
                    with suppress_pydantic_serialization_warning():
                        result = structured.invoke(input, config=config, **kwargs)
                    if not self._is_valid_result(result):
                        errors.append(f"{provider}/{model_name}: 返回空结果(None/空对象)")
                        logger.warning(f"懒加载结构化模型返回空结果 {provider}/{model_name}，尝试下一个")
                        continue
                    logger.info(f"懒加载结构化模型调用成功: {provider}/{model_name}")
                    return result
                except Exception as e:
                    errors.append(f"{provider}/{model_name}: {type(e).__name__}: {e}")
                    logger.warning(f"懒加载结构化模型调用失败 {provider}/{model_name}: {type(e).__name__}: {e}")
                    continue

        error_detail = "; ".join(errors)
        logger.error(f"所有结构化模型调用均失败: {error_detail}")
        raise RuntimeError(
            f"模型连接超时，所有已配置的模型均不可用。"
            f"已尝试: {error_detail}。请检查 API Key 配置和网络连接。"
        )

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        """异步调用结构化模型，失败时自动切换（含懒加载候选）

        使用 suppress_pydantic_serialization_warning 上下文管理器局部抑制
        Pydantic v2 + OpenAI SDK 结构化输出的已知兼容性警告。
        """
        errors: list[str] = list(self._creation_errors)

        for provider, model_name, structured in self._models:
            try:
                with suppress_pydantic_serialization_warning():
                    result = await structured.ainvoke(input, config=config, **kwargs)
                if not self._is_valid_result(result):
                    error_msg = f"{provider}/{model_name}: 返回空结果(None/空对象)"
                    errors.append(error_msg)
                    logger.warning(f"结构化模型异步返回空结果 {provider}/{model_name}，尝试下一个模型")
                    continue
                logger.info(f"结构化模型异步调用成功: {provider}/{model_name}")
                return result
            except Exception as e:
                errors.append(f"{provider}/{model_name}: {type(e).__name__}: {e}")
                logger.warning(f"结构化模型异步调用失败 {provider}/{model_name}: {type(e).__name__}: {e}")
                continue

        # 预实例化模型都失败，尝试懒加载候选
        for pid, mname in self._lazy_candidates:
            lazy_result = self._resolve_lazy_candidate(pid, mname)
            if lazy_result is not None:
                provider, model_name, structured = lazy_result
                try:
                    with suppress_pydantic_serialization_warning():
                        result = await structured.ainvoke(input, config=config, **kwargs)
                    if not self._is_valid_result(result):
                        errors.append(f"{provider}/{model_name}: 返回空结果(None/空对象)")
                        logger.warning(f"懒加载结构化模型异步返回空结果 {provider}/{model_name}，尝试下一个")
                        continue
                    logger.info(f"懒加载结构化模型异步调用成功: {provider}/{model_name}")
                    return result
                except Exception as e:
                    errors.append(f"{provider}/{model_name}: {type(e).__name__}: {e}")
                    logger.warning(f"懒加载结构化模型异步调用失败 {provider}/{model_name}: {type(e).__name__}: {e}")
                    continue

        error_detail = "; ".join(errors)
        logger.error(f"所有结构化模型异步调用均失败: {error_detail}")
        raise RuntimeError(
            f"模型连接超时，所有已配置的模型均不可用。"
            f"已尝试: {error_detail}。请检查 API Key 配置和网络连接。"
        )


# ============== 运行时 Fallback 检测回调 ==============

class FallbackDetectionCallback(BaseCallbackHandler):
    """检测 LLM 运行时 fallback 的回调处理器

    当 LangChain 的 with_fallbacks() 在运行时切换到备选模型时，
    通过 on_llm_start/on_llm_end 回调检测实际使用的模型。

    继承 BaseCallbackHandler 以确保与 LangChain 回调管理器兼容
    （需要 run_inline 等属性）。

    用法:
        with FallbackDetectionCallback(expected_provider="ollama", expected_model="qwen3:8b") as fb:
            config["callbacks"] = [fb]
            # ... 执行 agent ...
        if fb.fallback_detected:
            # 发送降级提示
    """

    def __init__(self, expected_provider: str = "", expected_model: str = ""):
        super().__init__()
        self.expected_provider = expected_provider
        self.expected_model = expected_model
        self._llm_attempts: list[dict[str, Any]] = []
        self._successful_model: str | None = None
        self._successful_provider: str | None = None
        self._fallback_detected = False
        self._first_success = True

    @property
    def fallback_detected(self) -> bool:
        return self._fallback_detected

    @property
    def actual_provider(self) -> str | None:
        return self._successful_provider

    @property
    def actual_model(self) -> str | None:
        return self._successful_model

    def get_fallback_info(self) -> dict[str, str] | None:
        """如果检测到降级，返回降级信息；否则返回 None"""
        if not self._fallback_detected:
            return None
        return {
            "original_provider": self.expected_provider,
            "original_model": self.expected_model,
            "actual_provider": self._successful_provider or "",
            "actual_model": self._successful_model or "",
            "message": (
                f"模型 {self.expected_provider}/{self.expected_model} 运行时失败，"
                f"已自动切换到 {self._successful_provider}/{self._successful_model}"
            ),
        }

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        model_name = self._extract_model(serialized, **kwargs)
        self._llm_attempts.append({
            "run_id": str(run_id),
            "model": model_name,
            "success": False,
        })

    def on_llm_end(self, response: Any, *, run_id: Any = None, **kwargs: Any) -> None:
        rid = str(run_id)
        for attempt in self._llm_attempts:
            if attempt["run_id"] == rid:
                attempt["success"] = True
                break

        # 第一次成功的 LLM 调用就是实际使用的模型
        if self._first_success:
            for attempt in self._llm_attempts:
                if attempt["run_id"] == rid and attempt["success"]:
                    actual_model = attempt["model"]
                    # 从模型名推断 provider
                    actual_provider = self._infer_provider(actual_model)
                    self._successful_model = actual_model
                    self._successful_provider = actual_provider

                    # 检测是否降级
                    if (self.expected_provider and actual_provider and
                            actual_provider != self.expected_provider):
                        self._fallback_detected = True
                        logger.info(
                            f"LLM 运行时 fallback 检测: "
                            f"{self.expected_provider}/{self.expected_model} -> "
                            f"{actual_provider}/{actual_model}"
                        )
                    self._first_success = False
                    break

    def on_llm_error(self, error: Any, *, run_id: Any = None, **kwargs: Any) -> None:
        rid = str(run_id)
        for attempt in self._llm_attempts:
            if attempt["run_id"] == rid:
                attempt["success"] = False
                attempt["error"] = str(error)
                break

    def _extract_model(self, serialized: dict[str, Any], **kwargs: Any) -> str:
        if "kwargs" in serialized:
            kw = serialized["kwargs"]
            for key in ("model", "model_name"):
                if key in kw:
                    return kw[key]
        if "name" in serialized:
            return serialized["name"]
        invocation_params = kwargs.get("invocation_params", {})
        for key in ("model_name", "model"):
            if key in invocation_params:
                return invocation_params[key]
        return ""

    def _infer_provider(self, model_name: str) -> str:
        """从模型名推断 provider"""
        if not model_name:
            return ""
        for pid, cfg in get_model_registry().items():
            for m in cfg.get("models", []):
                if (isinstance(m, dict) and m.get("name") == model_name) or (isinstance(m, str) and m == model_name):
                    return pid
            if cfg.get("default_model") == model_name:
                return pid
        return ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False
