"""
Embedding 模型工厂

统一的 Embedding 模型创建入口，对齐 LLM 工厂的设计：
1. 使用各 provider 官方 SDK 真实初始化（不是 OpenAI 兼容的"伪兼容"）
2. 集成 InMemoryRateLimiter 速率限制
3. 多级 fallback：主提供商 → 备选 API → 本地模型
4. 单例缓存

参考：
- https://docs.langchain.com/oss/python/integrations/embeddings/index
- 与 llm_factory.get_chat_model 对齐（默认启用 fallback）
"""

from __future__ import annotations

import threading
from typing import Any

from langchain_core.embeddings import Embeddings

from Django_xm.apps.core.config import get_logger

from ..config import settings

logger = get_logger(__name__)


# ============== Embedding Provider 注册表 ==============
# 每一项定义一个可用的 embedding 来源（不依赖 OpenAI 兼容"伪兼容"）
# - id: 注册 key
# - factory: 创建函数，返回 Embeddings 实例
# - label: 日志标识
# - requires_key: 必需的属性（从 settings 读 key 是否已配置）
# - enabled: 总开关

EMBEDDING_PROVIDER_REGISTRY: list[dict] = [
    {
        "id": "openai",
        "label": "OpenAI(text-embedding-3-small)",
        "factory_path": "Django_xm.apps.ai_engine.providers.openai_embedding.create_embedding",
        "key_attr": "openai_api_key",
        "enabled": True,
        "supported_params": {"batch_size", "model"},
        "dimension": 1536,  # text-embedding-3-small
    },
    {
        "id": "ollama",
        "label": "Ollama 本地(bge-m3)",
        "factory_path": "Django_xm.apps.ai_engine.providers.ollama_embedding.create_embedding",
        "key_attr": None,  # Ollama 本地无需 API key
        "enabled": True,
        # OllamaEmbeddings 不接受 chunk_size 字段
        "supported_params": {"model"},
        "dimension": 1024,  # bge-m3
    },
    {
        "id": "baidu_qianfan",
        "label": "百度千帆(QianfanEmbeddingsEndpoint)",
        "factory_path": "Django_xm.apps.ai_engine.providers.qianfan_embedding.create_embedding",
        "key_attr": "baidu_qianfan_api_key",
        "enabled": True,
        "supported_params": {"model"},
        "dimension": 1536,  # QianfanEmbeddingsEndpoint 默认
    },
    {
        "id": "local",
        "label": "本地HuggingFace(BAAI/bge-small-zh-v1.5)",
        "factory_path": "Django_xm.apps.ai_engine.providers.local_embedding.create_embedding",
        "key_attr": None,  # 本地无需 key
        "enabled": False,  # 需安装 HuggingFace 依赖，默认禁用
        # HuggingFaceBgeEmbeddings 接受 model_name 而非 model
        "supported_params": {"model_name"},
        "dimension": 512,  # BAAI/bge-small-zh-v1.5
    },
]


# ============== Embedding 实例缓存（独立于 model_cache.py） ==============

from collections import OrderedDict

_embedding_instance_cache: OrderedDict[str, Embeddings] = OrderedDict()
_embedding_cache_lock = threading.Lock()
_EMBEDDING_CACHE_MAXSIZE = 8


def _embedding_cache_get(key: str):
    with _embedding_cache_lock:
        if key in _embedding_instance_cache:
            _embedding_instance_cache.move_to_end(key)
            return _embedding_instance_cache[key]
    return None


def _embedding_cache_set(key: str, value: Embeddings) -> None:
    with _embedding_cache_lock:
        _embedding_instance_cache[key] = value
        _embedding_instance_cache.move_to_end(key)
        while len(_embedding_instance_cache) > _EMBEDDING_CACHE_MAXSIZE:
            _embedding_instance_cache.popitem(last=False)


def _make_embedding_cache_key(provider_id: str, model: str, batch_size: int) -> str:
    return f"emb:{provider_id}:{model}:bs{batch_size}"


def _import_factory(factory_path: str):
    """按路径字符串导入工厂函数"""
    module_path, _, attr = factory_path.rpartition(".")
    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, attr)


def _provider_available(provider_cfg: dict) -> bool:
    """检查 provider 是否可用（未禁用 + key 已配置）"""
    if not provider_cfg.get("enabled", True):
        return False
    key_attr = provider_cfg.get("key_attr")
    if key_attr is None:
        return True  # 本地无需 key
    key_value = getattr(settings, key_attr, "")
    return bool(key_value and key_value.strip())


def get_embedding_fallback_chain() -> list[dict]:
    """获取可用的 embedding provider 列表（按注册表顺序）"""
    from Django_xm.apps.ai_engine.services.registry_service import get_embedding_registry

    available = []
    for cfg in get_embedding_registry():
        if _provider_available(cfg):
            available.append(cfg)
    return available


def _create_single_embedding(
    provider_cfg: dict,
    batch_size: int,
    dimension: int | None = None,
    **kwargs: Any,
) -> Embeddings:
    """创建单个 embedding 实例（带缓存）

    自动按 supported_params 过滤通用参数（batch_size / model），
    避免 pydantic extra_forbidden 错误。

    Args:
        provider_cfg: provider 配置
        batch_size: 批处理大小
        dimension: MRL 截断维度（可选）。仅支持 Matryoshka 截断的 provider
            （Ollama MRL 模型、OpenAI text-embedding-3-*）会真正生效。
        **kwargs: 透传给 provider factory
    """
    model = kwargs.get("model", "") or provider_cfg.get("default_model", "")
    cache_key = _make_embedding_cache_key(provider_cfg["id"], model, batch_size)
    cached = _embedding_cache_get(cache_key)
    if cached is not None:
        logger.debug(f"Embedding 缓存命中: {provider_cfg['label']}")
        return cached

    supported = provider_cfg.get("supported_params", set())
    init_kwargs: dict[str, Any] = {}
    if "batch_size" in supported and batch_size:
        init_kwargs["batch_size"] = batch_size
    if "model" in supported and model:
        init_kwargs["model"] = model
    if "model_name" in supported and model:
        init_kwargs["model_name"] = model
    # 透传 MRL 截断维度
    if dimension is not None and dimension > 0:
        init_kwargs["dimensions"] = dimension
    # 透传其他明确支持的 kwargs
    for k, v in kwargs.items():
        if k in ("model",) and ("model" in supported or "model_name" in supported):
            continue  # 已通过 supported 字段处理
        if k in supported:
            init_kwargs[k] = v

    factory = _import_factory(provider_cfg["factory_path"])
    # 初始化失败抛出异常，让上层 try/except 排除此 provider
    embedding = factory(**init_kwargs)

    _embedding_cache_set(cache_key, embedding)
    return embedding


class FallbackEmbedding(Embeddings):
    """带自动 fallback 的 Embeddings 包装器

    主提供商失败时（403/余额不足/超时等），自动切换到下一个可用提供商。
    支持维度感知：当 required_dimension 不为 None 时，只在维度兼容的 provider 之间 fallback。
    设计参考 llm_factory.get_chat_model（默认启用 fallback）。
    """

    def __init__(
        self,
        embeddings_list: list[Embeddings],
        labels: list[str],
        required_dimension: int | None = None,
        provider_ids: list[str] | None = None,
    ):
        self._embeddings_list = embeddings_list
        self._labels = labels
        self._provider_ids = provider_ids or []
        self._active_index = 0
        self._required_dimension = required_dimension
        # 维度探测缓存：{provider 索引: 维度}
        self._dimension_cache: dict[int, int] = {}
        # 是否已完成维度探测
        self._dimensions_probed = False
        # 降级事件记录
        self._fallback_events: list[dict[str, str]] = []

    def _probe_dimensions(self) -> None:
        """懒加载：对每个 provider 调用 embed_query("test") 获取输出维度

        只在第一次需要时调用，结果缓存到 _dimension_cache。
        探测失败的 provider 不记入缓存，后续 fallback 时跳过。
        """
        if self._dimensions_probed:
            return
        self._dimensions_probed = True
        for idx, emb in enumerate(self._embeddings_list):
            if idx in self._dimension_cache:
                continue
            try:
                vec = emb.embed_query("test")
                dim = len(vec)
                self._dimension_cache[idx] = dim
                logger.debug(f"Embedding 维度探测: {self._labels[idx]} -> 维度={dim}")
            except Exception as e:
                logger.warning(f"Embedding 维度探测失败: {self._labels[idx]} -> {e}")

    def _get_compatible_providers(self) -> list[int]:
        """返回维度匹配的 provider 索引列表

        如果 required_dimension 为 None，返回所有 provider 索引（不限制维度）。
        如果没有维度兼容的 provider，抛出 ValueError。
        """
        if self._required_dimension is None:
            return list(range(len(self._embeddings_list)))

        # 确保维度已探测
        self._probe_dimensions()

        compatible = [idx for idx, dim in self._dimension_cache.items() if dim == self._required_dimension]

        if not compatible:
            # 收集所有已探测维度用于错误提示
            dim_info = ", ".join(f"{self._labels[idx]}={dim}" for idx, dim in sorted(self._dimension_cache.items()))
            raise ValueError(
                f"无维度兼容的 Embedding provider：要求维度={self._required_dimension}，已有 provider 维度: {dim_info}"
            )

        logger.debug(
            f"Embedding 维度过滤: 要求={self._required_dimension}，"
            f"兼容 provider: {[self._labels[i] for i in compatible]}"
        )
        return compatible

    def _record_fallback(self, from_idx: int, to_idx: int, reason: str = ""):
        """记录降级事件"""
        from_label = self._labels[from_idx]
        to_label = self._labels[to_idx]
        self._fallback_events.append(
            {
                "from_provider": self._provider_ids[from_idx] if from_idx < len(self._provider_ids) else from_label,
                "from_label": from_label,
                "to_provider": self._provider_ids[to_idx] if to_idx < len(self._provider_ids) else to_label,
                "to_label": to_label,
                "reason": reason,
            }
        )

    def get_fallback_events(self) -> list[dict[str, str]]:
        """获取所有降级事件"""
        return list(self._fallback_events)

    def get_active_provider_id(self) -> str | None:
        """获取当前活跃的 provider ID"""
        if self._provider_ids and self._active_index < len(self._provider_ids):
            return self._provider_ids[self._active_index]
        return None

    def _call_with_fallback(self, method_name: str, *args, **kwargs):
        # 每次调用前清空降级事件，避免缓存实例导致误报
        self._fallback_events = []
        last_error: Exception | None = None

        # 获取候选 provider 索引列表
        candidate_indices = self._get_compatible_providers()

        for offset in range(len(candidate_indices)):
            idx = candidate_indices[
                (
                    candidate_indices.index(self._active_index) + offset
                    if self._active_index in candidate_indices
                    else offset
                )
                % len(candidate_indices)
            ]
            emb = self._embeddings_list[idx]
            try:
                result = getattr(emb, method_name)(*args, **kwargs)
                if idx != self._active_index:
                    logger.info(
                        f"Embedding fallback 成功切换: {self._labels[self._active_index]} -> {self._labels[idx]}"
                    )
                    self._record_fallback(self._active_index, idx, str(last_error) if last_error else "")
                    self._active_index = idx
                return result
            except Exception as e:
                last_error = e
                logger.warning(f"Embedding 提供商 {self._labels[idx]} 调用 {method_name} 失败: {e}")
                continue
        logger.error(f"所有 Embedding 提供商均失败，最后错误: {last_error}")
        raise last_error  # type: ignore[misc]

    async def _acall_with_fallback(self, method_name: str, *args, **kwargs):
        # 每次调用前清空降级事件，避免缓存实例导致误报
        self._fallback_events = []
        last_error: Exception | None = None

        # 获取候选 provider 索引列表
        candidate_indices = self._get_compatible_providers()

        for offset in range(len(candidate_indices)):
            idx = candidate_indices[
                (
                    candidate_indices.index(self._active_index) + offset
                    if self._active_index in candidate_indices
                    else offset
                )
                % len(candidate_indices)
            ]
            emb = self._embeddings_list[idx]
            try:
                result = await getattr(emb, method_name)(*args, **kwargs)
                if idx != self._active_index:
                    logger.info(
                        f"Embedding fallback 成功切换: {self._labels[self._active_index]} -> {self._labels[idx]}"
                    )
                    self._record_fallback(self._active_index, idx, str(last_error) if last_error else "")
                    self._active_index = idx
                return result
            except Exception as e:
                last_error = e
                logger.warning(f"Embedding 提供商 {self._labels[idx]} 异步调用 {method_name} 失败: {e}")
                continue
        logger.error(f"所有 Embedding 提供商均失败，最后错误: {last_error}")
        raise last_error  # type: ignore[misc]

    def embed_documents(self, texts):
        return self._call_with_fallback("embed_documents", texts)

    def embed_query(self, text):
        return self._call_with_fallback("embed_query", text)

    async def aembed_documents(self, texts):
        return await self._acall_with_fallback("aembed_documents", texts)

    async def aembed_query(self, text):
        return await self._acall_with_fallback("aembed_query", text)


# ============== 公开 API ==============

_embedding_fallback_cache: dict = {}
_embedding_fallback_lock = threading.Lock()


def get_embeddings_with_fallback(
    model: str | None = None,
    batch_size: int = 100,
    use_cache: bool = True,
    use_fallback: bool = True,
    preferred_provider: str | None = None,
    required_dimension: int | None = None,
    dimension: int | None = None,
    **kwargs: Any,
) -> Embeddings:
    """获取带 fallback 的 Embeddings 实例

    Args:
        model: 模型名（可选，不传则使用 provider 默认）
        batch_size: 批处理大小
        use_cache: 是否启用 Redis embedding 缓存
        use_fallback: 是否启用多 provider fallback
        preferred_provider: 优先使用的 provider id（如 "openai"/"baidu_qianfan"/"local"）
        required_dimension: 要求的输出维度（可选，设置后只在维度匹配的 provider 之间 fallback）
        dimension: MRL 截断目标维度（可选）。仅支持 Matryoshka 截断的 provider
            会真正生效（Ollama MRL 模型、OpenAI text-embedding-3-*）。
        **kwargs: 透传给 provider factory

    Returns:
        Embeddings 实例（可能是 FallbackEmbedding 包装器）

    Raises:
        RuntimeError: 所有 provider 不可用时
        ValueError: required_dimension 不为 None 但无维度兼容的 provider
    """
    # ===== 提前构建 cache_key 并检查缓存，避免重复 DB 查询和日志输出 =====
    user_primary_id = preferred_provider
    user_fallback_id: str | None = None
    try:
        from Django_xm.apps.ai_engine.models import SystemConfig

        if not user_primary_id:
            emb_cfg = SystemConfig.get_value("embedding_provider", {})
            user_primary_id = emb_cfg.get("provider_id", "") or None
        fb_emb_config = SystemConfig.get_value("fallback_embedding_provider", {})
        user_fallback_id = fb_emb_config.get("provider_id", "") or None
    except Exception:
        # 配置读取失败时回退到默认 provider，不影响主流程
        logger.debug("读取 embedding provider 配置失败，使用默认值")

    # 读取 MRL 维度配置（缓存 key 需要）
    user_mrl_dimension = dimension
    if user_mrl_dimension is None:
        try:
            from Django_xm.apps.ai_engine.models import SystemConfig

            emb_cfg = SystemConfig.get_value("embedding_provider", {})
            cfg_dim = emb_cfg.get("dimension")
            cfg_pid = emb_cfg.get("provider_id", "")
            active_pid = user_primary_id or "default"
            if cfg_dim and (not cfg_pid or cfg_pid == active_pid):
                user_mrl_dimension = int(cfg_dim)
        except Exception:
            # MRL 维度配置读取失败时回退到传入维度，不影响主流程
            logger.debug("读取 MRL 维度配置失败，使用传入维度")

    cache_key = (
        f"emb_fb:{user_primary_id or 'default'}:"
        f"{model or ''}:{batch_size}:dim{required_dimension or 'any'}:mrl{user_mrl_dimension or 'off'}"
    )
    with _embedding_fallback_lock:
        if use_cache and cache_key in _embedding_fallback_cache:
            logger.debug(f"Embedding fallback 缓存命中: {cache_key}")
            return _embedding_fallback_cache[cache_key]

    # ===== 缓存未命中，执行完整逻辑 =====
    providers = get_embedding_fallback_chain()
    if not providers:
        raise RuntimeError(
            "无可用 Embedding 提供商：未配置任何 API key，且本地 provider 也未启用。"
            "请在 .env 中设置 OPENAI_API_KEY / BAIDU_QIANFAN_API_KEY 等。"
        )

    # 当用户明确配置了主/备选时，只保留这两个 provider，不加载无关 provider
    all_providers = providers  # 保留完整列表，维度不匹配时回退用
    if user_primary_id or user_fallback_id:
        allowed_ids = {pid for pid in (user_primary_id, user_fallback_id) if pid}
        filtered = [p for p in providers if p["id"] in allowed_ids]
        if filtered:
            providers = filtered
            logger.debug(f"Embedding 按用户配置过滤: 只保留 {allowed_ids}，共 {len(providers)} 个 provider")

    # 调整优先级：preferred_provider 置顶
    if user_primary_id:
        providers = sorted(providers, key=lambda p: 0 if p["id"] == user_primary_id else 1)

    # 用户配置的降级 Embedding 排在第二位
    if user_fallback_id and user_fallback_id != user_primary_id:
        fb_item = [p for p in providers if p["id"] == user_fallback_id]
        others = [p for p in providers if p["id"] != user_fallback_id]
        if fb_item:
            if others and others[0]["id"] == user_primary_id:
                providers = [others[0], *fb_item, *others[1:]]
            else:
                providers = fb_item + others

    # MRL 维度已在上方提前读取（user_mrl_dimension）

    # 维度预过滤：综合考虑精确匹配 + MRL 截断兼容
    if required_dimension is not None:

        def _is_dimension_compatible(p: dict) -> bool:
            """判断 provider 在 MRL 截断/固定维度下能否输出指定维度

            规则：
            1. MRL 模型（min_dimension > 0 且 native_max_dimension > 0）：
               [min_dimension, native_max_dimension] 范围匹配
            2. 固定维度模型：dimension 精确匹配
            """
            native_max = p.get("native_max_dimension") or 0
            min_dim = p.get("min_dimension") or 0
            if native_max > 0 and min_dim > 0 and min_dim <= required_dimension <= native_max:
                return True
            # 固定维度精确匹配
            return p.get("dimension") == required_dimension

        dim_filtered = [p for p in providers if _is_dimension_compatible(p)]
        if dim_filtered:
            logger.info(
                f"Embedding 维度预过滤: 要求维度={required_dimension}，"
                f"注册表匹配 {len(dim_filtered)}/{len(providers)} 个 provider"
            )
            providers = dim_filtered
        else:
            # 用户配置的 provider 均不满足维度要求，从完整注册表中补充维度兼容的 provider
            all_dim_matched = [p for p in all_providers if _is_dimension_compatible(p)]
            if all_dim_matched:
                # 合并：用户配置的 provider 优先 + 维度兼容的补充 provider
                existing_ids = {p["id"] for p in providers}
                extra = [p for p in all_dim_matched if p["id"] not in existing_ids]
                providers = providers + extra
                logger.warning(
                    f"Embedding 维度回退: 用户配置的 provider 无维度={required_dimension} 匹配，"
                    f"从注册表补充 {len(extra)} 个维度兼容 provider: "
                    f"{[p['id'] for p in extra]}"
                )
            else:
                # 注册表中也无匹配，保留全部 provider，交由运行时维度探测处理
                dim_info = ", ".join(
                    f"{p['id']}={p.get('dimension', '未知')}/max{p.get('native_max_dimension', 0)}" for p in providers
                )
                logger.warning(
                    f"Embedding 维度预过滤: 注册表中无维度={required_dimension} 的 provider，"
                    f"已有: {dim_info}，将交由运行时维度探测"
                )

    logger.info(
        f"Embedding 初始化: {len(providers)} 个可用 provider, "
        f"preferred={user_primary_id or providers[0]['id']}"
        f"{f', required_dimension={required_dimension}' if required_dimension else ''}"
    )

    # 缓存 key 已在函数入口提前构建并检查

    embeddings_list: list[Embeddings] = []
    labels: list[str] = []
    errors: list[str] = []

    for provider_cfg in providers:
        try:
            emb = _create_single_embedding(
                provider_cfg,
                batch_size=batch_size,
                model=model,
                dimension=user_mrl_dimension,
                **kwargs,
            )
            embeddings_list.append(emb)
            labels.append(provider_cfg["label"])
        except Exception as e:
            errors.append(f"{provider_cfg['label']}: {e}")
            logger.warning(f"Embedding provider {provider_cfg['label']} 初始化失败: {e}")

    if not embeddings_list:
        raise RuntimeError(f"所有 Embedding provider 初始化失败: {'; '.join(errors)}")

    result: Embeddings
    if use_fallback and len(embeddings_list) > 1:
        result = FallbackEmbedding(
            embeddings_list,
            labels,
            required_dimension=required_dimension,
            provider_ids=[p["id"] for p in providers],
        )
        logger.info(
            f"Embedding fallback 已就绪: {len(embeddings_list)} 个 provider"
            f"{f'，维度限制={required_dimension}' if required_dimension else ''}"
        )
    else:
        result = embeddings_list[0]
        logger.info(f"Embedding 单 provider 模式: {labels[0]}")

    # 包装 Redis 缓存
    if use_cache:
        from Django_xm.apps.knowledge.services.embedding_service import CachedEmbeddings

        result = CachedEmbeddings(result, model=model or "default")

    with _embedding_fallback_lock:
        _embedding_fallback_cache[cache_key] = result

    return result


def reset_embedding_factory() -> None:
    """重置单例缓存（供测试）"""
    global _embedding_fallback_cache  # noqa: PLW0603 - 模块级测试缓存重置
    with _embedding_fallback_lock:
        _embedding_fallback_cache = {}


# ============== 数据库配置读取 ==============


def get_system_embedding_provider() -> str | None:
    """从 SystemConfig 数据库读取用户偏好的 Embedding provider

    优先级：SystemConfig 数据库 > .env 配置 > None
    """
    try:
        from Django_xm.apps.ai_engine.models import SystemConfig

        config = SystemConfig.get_value("embedding_provider", {})
        provider_id = config.get("provider_id", "")
        if provider_id:
            return provider_id
    except Exception:
        # 配置读取失败时返回 None，使用默认 provider
        logger.debug("读取 embedding provider 配置失败，返回 None")
    return None


# ============== 维度探测工具 ==============

_dimension_detect_cache: dict[int, int] = {}
_dimension_detect_lock = threading.Lock()


def detect_embedding_dimension(embedding: Embeddings) -> int:
    """探测 Embeddings 实例的输出维度

    调用 embed_query("test") 获取实际输出维度，带缓存避免重复调用。

    Args:
        embedding: Embeddings 实例

    Returns:
        输出向量维度

    Raises:
        RuntimeError: 探测失败时
    """
    # 使用实例 id 作为缓存 key
    emb_id = id(embedding)
    with _dimension_detect_lock:
        if emb_id in _dimension_detect_cache:
            return _dimension_detect_cache[emb_id]

    try:
        vec = embedding.embed_query("test")
        dim = len(vec)
        with _dimension_detect_lock:
            _dimension_detect_cache[emb_id] = dim
        logger.debug(f"Embedding 维度探测成功: 维度={dim}")
        return dim
    except Exception as e:
        raise RuntimeError(f"Embedding 维度探测失败: {e}") from e
