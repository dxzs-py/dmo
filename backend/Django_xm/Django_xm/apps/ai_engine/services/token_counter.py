"""
Token 使用量回调处理器 - LangChain v1.2.13 兼容

替代 langchain_community.callbacks.manager.get_openai_callback，
使用 LangChain v1.2.x 原生的 BaseCallbackHandler 实现 token 计数。

LangChain v1.2.x 中，AIMessage.usage_metadata 包含 token 使用信息，
本模块通过回调机制在流式和非流式场景下统一收集 token 数据。
"""

from typing import Any, Dict, Optional
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult


class TokenUsageCallbackHandler(BaseCallbackHandler):
    """Token 使用量回调处理器"""

    def __init__(self):
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.total_tokens: int = 0
        self.total_cost: float = 0.0
        self.successful_requests: int = 0

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """LLM 调用结束时收集 token 使用信息"""
        if response.llm_output and "token_usage" in response.llm_output:
            usage = response.llm_output["token_usage"]
            self.prompt_tokens += usage.get("prompt_tokens", 0)
            self.completion_tokens += usage.get("completion_tokens", 0)
            self.total_tokens += usage.get("total_tokens", 0)
            self.successful_requests += 1
            return

        for generation in response.generations:
            for gen in generation:
                if isinstance(gen, dict):
                    msg = gen.get("message", gen.get("text", ""))
                else:
                    msg = getattr(gen, "message", None) or getattr(gen, "text", None)

                if isinstance(msg, AIMessage) and msg.usage_metadata:
                    um = msg.usage_metadata
                    self.prompt_tokens += um.get("input_tokens", 0)
                    self.completion_tokens += um.get("output_tokens", 0)
                    self.total_tokens += um.get("total_tokens", 0)
                    self.successful_requests += 1

    def reset(self) -> None:
        """重置计数器"""
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.total_cost = 0.0
        self.successful_requests = 0

    def __enter__(self):
        self.reset()
        return self

    def __exit__(self, *args):
        pass
