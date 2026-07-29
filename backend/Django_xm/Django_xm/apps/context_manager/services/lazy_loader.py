from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)


class LazyLoader:
    """延迟加载系统

    启动时仅加载元数据/摘要，完整内容按需加载。
    典型场景：工具描述、MCP schema、子目录规则等。
    """

    def __init__(self, max_total_tokens=25000):
        self._registry = {}  # {key: {summary, loader_fn, loaded, content, max_tokens}}
        self._max_total_tokens = max_total_tokens
        self._loaded_tokens = 0

    def register(self, key: str, summary: str, loader_fn: callable, max_tokens: int = 5000):
        """注册延迟加载项

        Args:
            key: 唯一标识（如工具名）
            summary: 约 450 tokens 的摘要（用于 system prompt）
            loader_fn: 完整内容加载函数（无参数，返回 str）
            max_tokens: 完整内容的最大 token 数
        """
        if key in self._registry:
            return  # 已注册，跳过
        self._registry[key] = {
            "summary": summary,
            "loader_fn": loader_fn,
            "loaded": False,
            "content": None,
            "max_tokens": max_tokens,
        }

    def load(self, key: str) -> str:
        """按需加载完整内容

        如果总 token 数超过上限，返回摘要而非完整内容。
        """
        item = self._registry.get(key)
        if not item:
            return ""
        if item["loaded"]:
            return item["content"] or item["summary"]

        # 检查 token 预算
        if self._loaded_tokens + item["max_tokens"] > self._max_total_tokens:
            logger.warning(f"LazyLoader: token 预算不足，返回摘要: {key}")
            return item["summary"]

        # 执行加载
        try:
            content = item["loader_fn"]()
            item["content"] = content
            item["loaded"] = True
            # 粗略估算 token 数（中文约 1.5 字符/token，英文约 4 字符/token）
            estimated_tokens = len(content) // 3
            self._loaded_tokens += min(estimated_tokens, item["max_tokens"])
            return content
        except Exception as e:
            logger.warning(f"LazyLoader: 加载失败: {key}, {e}")
            return item["summary"]

    def get_context(self, keys: list | None = None) -> str:
        """获取上下文（已加载项返回完整内容，未加载项返回摘要）"""
        items = self._registry.items()
        if keys:
            items = [(k, v) for k, v in items if k in keys]

        parts = []
        for _key, item in items:
            if item["loaded"]:
                parts.append(item["content"] or item["summary"])
            else:
                parts.append(item["summary"])
        return "\n\n".join(parts)

    def get_summaries(self) -> str:
        """获取所有注册项的摘要（用于 system prompt）"""
        summaries = [item["summary"] for item in self._registry.values()]
        return "\n\n".join(summaries)

    def is_loaded(self, key: str) -> bool:
        """检查是否已加载"""
        item = self._registry.get(key)
        return item["loaded"] if item else False

    def get_loaded_keys(self) -> list:
        """获取已加载的 key 列表"""
        return [k for k, v in self._registry.items() if v["loaded"]]

    def reset(self):
        """重置所有状态"""
        for item in self._registry.values():
            item["loaded"] = False
            item["content"] = None
        self._loaded_tokens = 0
