"""
深度思考公共方法

统一管理深度思考相关方法，供普通对话和深度研究共用：
- extract_thinking_content: 从流式 chunk 提取思考内容（多厂商兼容）
- is_thinking_enabled: 判断深度思考是否启用
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def extract_thinking_content(chunk) -> str | None:
    """从流式 chunk 中提取思考内容，兼容多种 Provider 格式。

    - DeepSeek: additional_kwargs["reasoning_content"]
    - Ollama (Qwen3/R1): additional_kwargs["reasoning_content"]（langchain-ollama 自动处理）
    - Anthropic Claude: content blocks 中 type="thinking" 的块
    """
    # 1. DeepSeek / Ollama: additional_kwargs["reasoning_content"]
    additional_kwargs = getattr(chunk, "additional_kwargs", {})
    reasoning_content = additional_kwargs.get("reasoning_content")
    if reasoning_content and isinstance(reasoning_content, str):
        return reasoning_content

    # 2. Anthropic Claude: thinking content blocks
    # ChatAnthropic 将思考内容放在 content blocks 中，type="thinking"
    content = getattr(chunk, "content", None)
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "thinking":
                text = block.get("text", "")
                if text:
                    return text
            # LangChain ContentBlock 对象
            if hasattr(block, "type") and block.type == "thinking":
                text = getattr(block, "text", "")
                if text:
                    return text

    return None


def is_thinking_enabled(special_params: dict[str, Any], provider_id: str = "") -> bool:
    """通用判断深度思考是否启用，兼容 DeepSeek/Ollama/Anthropic 等多 Provider。

    - DeepSeek: thinking={"type": "enabled"} 或 reasoning_effort 存在
    - Ollama: thinking=True 或 reasoning=True
    - Anthropic: thinking={"type": "enabled", "budget_tokens": N}
    """
    if not special_params:
        return False
    # 通用检查：thinking 参数
    thinking_val = special_params.get("thinking")
    if thinking_val:
        # DeepSeek/Anthropic 格式: {"type": "enabled", ...}
        if isinstance(thinking_val, dict) and thinking_val.get("type") == "enabled":
            return True
        # Ollama 格式: True
        if isinstance(thinking_val, bool) and thinking_val:
            return True
    # DeepSeek 特有：reasoning_effort 存在时 thinking 自动启用
    if "reasoning_effort" in special_params:
        return True
    # Ollama 特有：reasoning 参数
    return bool(special_params.get("reasoning"))
