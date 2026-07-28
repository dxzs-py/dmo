"""
Token 使用量回调处理器 - LangChain v1.2.13 兼容

替代 langchain_community.callbacks.manager.get_openai_callback，
使用 LangChain v1.2.x 原生的 BaseCallbackHandler 实现 token 计数。

LangChain v1.2.x 中，AIMessage.usage_metadata 包含 token 使用信息，
本模块通过回调机制在流式和非流式场景下统一收集 token 数据。
"""

import logging
import threading
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult

_logger = logging.getLogger(__name__)


def _extract_model_name(serialized: dict[str, Any], **kwargs: Any) -> str:
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


class TokenUsageCallbackHandler(BaseCallbackHandler):
    """Token 使用量回调处理器"""

    def __init__(self, model_name: str = ""):
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.total_tokens: int = 0
        self.successful_requests: int = 0
        self._llm_start_times: dict[str, float] = {}
        self._current_model: str = model_name
        self._lock = threading.Lock()

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        import time
        self._llm_start_times[str(run_id)] = time.time()
        model = _extract_model_name(serialized, **kwargs)
        if model:
            self._current_model = model

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        prompt = 0
        completion = 0
        total = 0

        if response.llm_output and "token_usage" in response.llm_output:
            usage = response.llm_output["token_usage"]
            prompt = usage.get("prompt_tokens", 0)
            completion = usage.get("completion_tokens", 0)
            total = usage.get("total_tokens", 0)
        else:
            for generation in response.generations:
                for gen in generation:
                    if isinstance(gen, dict):
                        msg = gen.get("message", gen.get("text", ""))
                    else:
                        msg = getattr(gen, "message", None) or getattr(gen, "text", None)

                    if isinstance(msg, AIMessage) and msg.usage_metadata:
                        um = msg.usage_metadata
                        prompt += um.get("input_tokens", 0)
                        completion += um.get("output_tokens", 0)
                        total += um.get("total_tokens", 0)

        with self._lock:
            self.prompt_tokens += prompt
            self.completion_tokens += completion
            self.total_tokens += total
            if prompt > 0 or completion > 0:
                self.successful_requests += 1

            run_id = kwargs.get("run_id")
            if run_id is not None:
                self._llm_start_times.pop(str(run_id), None)

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        run_id = kwargs.get("run_id")
        _logger.warning("LLM error occurred for run %s", run_id)

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        pass

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        pass

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        pass

    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        pass

    def on_chain_end(
        self,
        outputs: dict[str, Any],
        *,
        run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        pass

    def on_chain_error(self, error: BaseException, **kwargs: Any) -> None:
        pass

    def reset(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.successful_requests = 0
        self._llm_start_times.clear()

    def __enter__(self):
        self.reset()
        return self

    def __exit__(self, *args):
        pass
