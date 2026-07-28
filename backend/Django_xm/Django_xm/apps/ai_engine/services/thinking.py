"""
深度思考公共方法

统一管理深度思考相关方法，供普通对话和深度研究共用：
- extract_thinking_content: 从流式 chunk 提取思考内容（多厂商兼容）
- inject_thinking_params: 注入厂商特定的思考参数
- clean_thinking_params: 清除思考参数
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


def inject_thinking_params(
    init_kwargs: dict[str, Any],
    provider_id: str,
    provider: str,
    special_params: dict[str, Any] | None = None,
) -> None:
    """为指定 provider 注入正确格式的深度思考参数

    每个 provider 的深度思考参数格式和传递位置不同：
    - DeepSeek (langchain_openai.ChatOpenAI): extra_body.thinking={"type":"enabled"}, 移除 temperature/top_p，reasoning_effort 也放入 extra_body
    - Anthropic (langchain_anthropic.ChatAnthropic): thinking={"type":"enabled","budget_tokens":N} 作为顶层参数
    - Ollama (ChatOllama): reasoning=True (布尔值) 作为顶层参数
    - 其他支持 deep_thinking 的 provider: 仅记录日志
    """
    if provider_id == "deepseek" or provider == "deepseek":
        # DeepSeek 通过 OpenAI 兼容接口调用，thinking/reasoning_effort 必须通过 extra_body 传递，
        # 不能作为 ChatOpenAI 构造参数（否则会透传给 openai.AsyncCompletions.create 导致 unexpected keyword argument）
        extra_body = init_kwargs.get("extra_body")
        if not isinstance(extra_body, dict):
            extra_body = {}
            init_kwargs["extra_body"] = extra_body
        extra_body["thinking"] = {"type": "enabled"}
        # reasoning_effort 也必须通过 extra_body 传递
        re_value = None
        if special_params and "reasoning_effort" in special_params:
            re_value = special_params["reasoning_effort"]
        if re_value is not None:
            extra_body["reasoning_effort"] = re_value
        # DeepSeek 推理模式不支持 temperature/top_p 采样参数，必须移除
        init_kwargs.pop("temperature", None)
        init_kwargs.pop("top_p", None)
        logger.debug(f"DeepSeek 深度思考模式已启用: extra_body.thinking enabled, reasoning_effort={re_value}")
    elif provider_id == "anthropic" or provider == "anthropic":
        # ChatAnthropic 原生支持 thinking 顶层构造参数
        init_kwargs["thinking"] = {"type": "enabled", "budget_tokens": 10000}
        logger.debug("Anthropic 扩展思考模式已启用")
    elif provider_id == "ollama" or provider == "ollama":
        # ChatOllama 原生支持 reasoning 布尔构造参数
        init_kwargs["reasoning"] = True
        logger.debug("Ollama 深度思考模式已启用: reasoning=True")
    else:
        logger.debug(f"Provider {provider_id} 支持 deep_thinking 但无特殊参数注入")


def clean_thinking_params(init_kwargs: dict[str, Any]) -> None:
    """清理 init_kwargs 中所有深度思考相关参数，避免跨 provider 残留"""
    # 顶层参数
    for key in ("thinking", "reasoning", "reasoning_effort"):
        init_kwargs.pop(key, None)
    # model_kwargs 中的参数
    if "model_kwargs" in init_kwargs and isinstance(init_kwargs["model_kwargs"], dict):
        for key in ("thinking", "reasoning", "reasoning_effort"):
            init_kwargs["model_kwargs"].pop(key, None)
    # extra_body 中的参数
    if "extra_body" in init_kwargs and isinstance(init_kwargs["extra_body"], dict):
        for key in ("thinking", "reasoning", "reasoning_effort"):
            init_kwargs["extra_body"].pop(key, None)


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
    if special_params.get("reasoning"):
        return True
    return False
