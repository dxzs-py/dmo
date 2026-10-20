"""
Token 估算器（common 基础设施）

模型 token 上限单一权威表与 token 估算（tiktoken → transformers → 改进启发式）。
下沉自 ``apps.context_manager.services.compression``（spec eliminate-priority-debts
任务 4.1 环 1 破环：ai_engine 的 usage_tracker / capabilities.builtin 需要本估算器，
经 common 消费以避免 ai_engine → context_manager 边）。

消费方约定：本模块为纯函数性质工具，禁止引入 apps 层依赖。
"""

import logging
import time
from collections.abc import Callable
from typing import Any, ClassVar

from Django_xm.common.messages import content_to_str

try:
    import tiktoken as _tiktoken

    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _tiktoken = None
    _TIKTOKEN_AVAILABLE = False

try:
    from transformers import AutoTokenizer as _AutoTokenizer

    _TRANSFORMERS_AVAILABLE = True
except ImportError:
    _AutoTokenizer = None
    _TRANSFORMERS_AVAILABLE = False

logger = logging.getLogger(__name__)


class TokenEstimator:
    # 模型上下文窗口表（A12：扩充主流档位，取值遵循各官方文档常见配置，
    # 供预算规划使用，非硬保证；子串匹配，更具体的前缀必须放在更宽泛的前缀之前
    # ——迭代取第一个命中，如 "gpt-4o-mini" 须在 "gpt-4o" 之前、"o1-mini" 在 "o1" 之前）
    _MODEL_LIMITS: ClassVar[dict[str, int]] = {
        # OpenAI GPT / o / GPT-5 系列
        "gpt-4o-mini": 128000,
        "gpt-4o": 128000,
        "gpt-4-turbo": 128000,
        "gpt-4": 8192,
        "gpt-3.5-turbo": 16385,
        "o1-mini": 128000,
        "o1-preview": 128000,
        "o1": 200000,
        "o3-mini": 200000,
        "o3": 200000,
        "o4-mini": 200000,
        "gpt-5": 400000,
        # Claude 系列（全系 200K 上下文，含日期版本号变体）
        "claude-sonnet-4-5": 200000,
        "claude-haiku-4-5": 200000,
        "claude-opus-4": 200000,
        "claude-sonnet-4": 200000,
        "claude-3-7-sonnet": 200000,
        "claude-3-5-sonnet": 200000,
        "claude-3-5-haiku": 200000,
        "claude-3-opus": 200000,
        "claude-3-haiku": 200000,
        "claude": 200000,
        # DeepSeek 系列（V3.x 起主流档位 128K）
        "deepseek-chat": 128000,
        "deepseek-reasoner": 128000,
        "deepseek": 128000,
        # Gemini 系列
        "gemini-2.5-pro": 1048576,
        "gemini-2.5-flash": 1048576,
        "gemini-2.0-flash-exp": 1000000,
        "gemini-2.0-flash": 1048576,
        "gemini-1.5-pro": 2097152,
        "gemini-1.5-flash": 1048576,
        "gemini-pro": 32768,
        "gemini": 1048576,
        # Qwen 系列（DashScope 商业档 + 开源主流档）
        "qwen-max": 32768,
        "qwen-plus": 131072,
        "qwen-turbo": 1000000,
        "qwen2.5": 32768,
        "qwen3": 32768,
        "qwen": 32768,
        # OpenAI 开源 GPT-OSS 系列（官方窗口 131072）
        "gpt-oss": 131072,
    }

    # tiktoken 编码映射：**仅 GPT 系**有官方对应 encoding（tiktoken 不提供
    # claude/deepseek/qwen 的官方 encoding，不得臆造近似映射）。
    # claude / deepseek / qwen 三系按 _TRANSFORMERS_ONLY_FAMILIES 路由到
    # transformers 分支（_estimate_with_transformers 已有各系专用 tokenizer，
    # claude → Xenova/claude-tokenizer、deepseek → deepseek-llm-7b-base、
    # qwen → Qwen2-7B）；其余未知模型维持 cl100k_base 近似（通用性最好的
    # BPE 近似），两分支均失败时最终回退启发式估算。
    _MODEL_ENCODING_MAP: ClassVar[dict[str, str]] = {
        "gpt-4o": "o200k_base",
        "gpt-4o-mini": "o200k_base",
        "gpt-4-turbo": "cl100k_base",
        "gpt-4": "cl100k_base",
        "gpt-3.5-turbo": "cl100k_base",
    }

    # 无官方 tiktoken encoding、须走 transformers 精确估算的模型家族前缀
    _TRANSFORMERS_ONLY_FAMILIES: ClassVar[tuple[str, ...]] = ("claude", "deepseek", "qwen")

    _DEFAULT_ENCODING: str = "cl100k_base"

    _encoding_cache: ClassVar[dict[str, Any]] = {}

    # transformers tokenizer 缓存
    _transformers_tokenizer_cache: ClassVar[dict[str, Any]] = {}

    @classmethod
    def _get_encoding(cls, model_name: str = "") -> Any:
        if not _TIKTOKEN_AVAILABLE:
            return None
        encoding_name = cls._DEFAULT_ENCODING
        if model_name:
            for key, enc in cls._MODEL_ENCODING_MAP.items():
                if key in model_name:
                    encoding_name = enc
                    break
            else:
                # 无 GPT 系映射命中：claude/deepseek/qwen 无官方 tiktoken
                # encoding，返回 None 使 estimate_tokens 落到 transformers
                # 分支（有各系专用 tokenizer）；其余未知模型维持 cl100k 近似
                model_lower = model_name.lower()
                if any(family in model_lower for family in cls._TRANSFORMERS_ONLY_FAMILIES):
                    return None
        if encoding_name not in cls._encoding_cache:
            try:
                cls._encoding_cache[encoding_name] = _tiktoken.get_encoding(encoding_name)
            except Exception as e:
                logger.warning(f"tiktoken 获取编码失败，编码名={encoding_name}: {e}")
                return None
        return cls._encoding_cache[encoding_name]

    @classmethod
    def _estimate_with_transformers(cls, text: str, model_name: str = "") -> int:
        """尝试使用 HuggingFace transformers tokenizer 进行精确估算"""
        if not _TRANSFORMERS_AVAILABLE or not text:
            return -1  # 返回 -1 表示不可用，与 0（空文本）区分

        # 根据模型名选择合适的 tokenizer
        tokenizer_name = "gpt2"  # 默认使用 GPT-2 tokenizer
        if model_name:
            model_lower = model_name.lower()
            if "deepseek" in model_lower:
                tokenizer_name = "deepseek-ai/deepseek-llm-7b-base"
            elif "claude" in model_lower:
                tokenizer_name = "Xenova/claude-tokenizer"
            elif "qwen" in model_lower:
                tokenizer_name = "Qwen/Qwen2-7B"

        # 使用缓存避免重复加载
        if tokenizer_name not in cls._transformers_tokenizer_cache:
            try:
                cls._transformers_tokenizer_cache[tokenizer_name] = _AutoTokenizer.from_pretrained(tokenizer_name)
            except Exception as e:
                logger.debug(f"transformers tokenizer 加载失败 ({tokenizer_name}): {e}")
                cls._transformers_tokenizer_cache[tokenizer_name] = None

        tokenizer = cls._transformers_tokenizer_cache.get(tokenizer_name)
        if tokenizer is None:
            return -1

        try:
            return len(tokenizer.encode(text))
        except Exception as e:
            logger.debug(f"transformers tokenizer 编码失败: {e}")
            return -1

    @staticmethod
    def _improved_rough_estimate(text: str) -> int:
        """改进的启发式估算：中文字符 x1.0、英文单词 x1.3、标点 x0.5、数字 x0.25/字符

        4.5 口径校准：中文按 1.0 token/字取保守值（tiktoken cl100k/o200k 实测
        中文约 0.6-1 token/字，原 1.5 系数系统性高估 50%，导致预算提前触发、
        压缩过度）。本启发式仅在 tiktoken/transformers 均不可用时兜底。
        """
        if not text:
            return 0

        import re

        # 中文字符
        chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        chinese_tokens = chinese_chars * 1.0

        # 英文单词（按词计算，每个词约 1.3 token）
        english_words = re.findall(r"[a-zA-Z]+", text)
        english_tokens = len(english_words) * 1.3

        # 标点符号
        punctuation_chars = sum(
            1 for c in text if not c.isalnum() and not c.isspace() and not ("\u4e00" <= c <= "\u9fff")
        )
        punctuation_tokens = punctuation_chars * 0.5

        # 数字（按字符计算，每个字符约 0.25 token）
        digit_chars = sum(1 for c in text if c.isdigit())
        digit_tokens = digit_chars * 0.25

        # 其他字符（空白等，少量开销）
        other_chars = len(text) - chinese_chars - sum(len(w) for w in english_words) - punctuation_chars - digit_chars
        other_tokens = max(0, other_chars) * 0.2

        return int(chinese_tokens + english_tokens + punctuation_tokens + digit_tokens + other_tokens)

    @classmethod
    def estimate_tokens(cls, text: str, model_name: str = "") -> int:
        """估算 token 数：优先 tiktoken → transformers → 改进启发式"""
        if not text:
            return 0

        # 1. 优先使用 tiktoken
        encoding = cls._get_encoding(model_name)
        if encoding is not None:
            try:
                return len(encoding.encode(text))
            except Exception as e:
                logger.debug(f"tiktoken 编码失败，尝试 transformers: {e}")

        # 2. 其次尝试 transformers tokenizer
        transformers_result = cls._estimate_with_transformers(text, model_name)
        if transformers_result >= 0:
            return transformers_result

        # 3. 最后回退到改进的启发式方法
        return cls._improved_rough_estimate(text)

    @staticmethod
    def estimate(text: str) -> int:
        return TokenEstimator.estimate_tokens(text)

    @classmethod
    def estimate_dict_messages(cls, messages: list[dict[str, Any]], model_name: str = "") -> int:
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += cls.estimate_tokens(content, model_name)
            elif isinstance(content, list):
                total += cls.estimate_tokens(content_to_str(content), model_name)
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    total += cls.estimate_tokens(str(tc.get("args", tc.get("function", {}))), model_name)
        return total

    @classmethod
    def get_model_limit(cls, model_name: str, default: int = 128000) -> int:
        """查询模型 token 上限。

        查询顺序：
        1. 管理配置自定义表（loader 注入，见 ``set_custom_limits_loader``，
           默认由 ai_engine 启动时注册，读取 SystemConfig ``model_context_limits``，
           admin 编辑后 ≤60s 自动生效）
        2. 内置 _MODEL_LIMITS（官方档位，子串匹配）

        Args:
            model_name: 模型名称，按子串匹配条目。
            default: 未命中或模型名为空时的回退上限。

        内置表也未命中时回退 default 并记录 warning（提示可在 admin 的
        SystemConfig 中配置 ``model_context_limits`` 键补档，无需改代码）。
        """
        if not model_name:
            return default
        custom = cls._get_custom_limits()
        if custom:
            # 精确命中优先，其次前缀/子串（与内置表匹配语义一致）
            if model_name in custom:
                return int(custom[model_name])
            for key, limit in custom.items():
                if key and key in model_name:
                    return int(limit)
        for key, limit in cls._MODEL_LIMITS.items():
            if key in model_name:
                return limit
        logger.warning(
            "未知模型 '%s' 不在 TokenEstimator 模型表中，回退默认上限 %d tokens；"
            "可在 Django admin 的 SystemConfig 中新增/编辑 model_context_limits 键"
            '（值形如 {"模型名或前缀": 窗口tokens}）补档，无需修改代码',
            model_name,
            default,
        )
        return default

    # ---- 管理配置自定义模型表（配置驱动，common 不依赖 apps：loader 由 apps 注入）----

    _custom_limits_loader: ClassVar[Callable[[], dict[str, int] | None] | None] = None
    _custom_limits: ClassVar[dict[str, int]] = {}
    _custom_limits_loaded_at: ClassVar[float] = 0.0
    _CUSTOM_LIMITS_TTL: ClassVar[float] = 60.0  # 秒；admin 改配置后最多延迟一个 TTL 生效

    @classmethod
    def set_custom_limits_loader(cls, loader: Callable[[], dict[str, int] | None] | None) -> None:
        """注册自定义模型表加载器（由 apps 层在启动时注入，common 不反向依赖）。

        loader 返回 {模型名或前缀: 窗口tokens} 映射；返回 None 或抛异常视为
        本次无配置（保留上次成功加载的表）。传 None 注销 loader。
        """
        cls._custom_limits_loader = loader
        cls._custom_limits = {}
        cls._custom_limits_loaded_at = 0.0

    @classmethod
    def _get_custom_limits(cls) -> dict[str, int]:
        now = time.monotonic()
        if cls._custom_limits_loaded_at and (now - cls._custom_limits_loaded_at) < cls._CUSTOM_LIMITS_TTL:
            return cls._custom_limits
        if cls._custom_limits_loader is None:
            return {}
        try:
            data = cls._custom_limits_loader()
        except Exception as e:  # 配置读取失败不应影响预算规划
            logger.warning(f"读取自定义模型窗口表失败，沿用上次配置: {type(e).__name__}: {e}")
            cls._custom_limits_loaded_at = now
            return cls._custom_limits
        if isinstance(data, dict) and data:
            cls._custom_limits = {str(k): int(v) for k, v in data.items() if v}
        elif isinstance(data, dict):
            cls._custom_limits = {}
        cls._custom_limits_loaded_at = now
        return cls._custom_limits
