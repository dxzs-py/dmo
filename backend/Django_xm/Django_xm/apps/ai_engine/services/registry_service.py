"""
数据库驱动的模型注册表服务层

替代 config.py 中的 MODEL_REGISTRY 和 embedding_factory.py 中的 EMBEDDING_PROVIDER_REGISTRY，
所有读取走数据库，Admin 中可直接增删改 Provider/Model/Embedding。

异步安全：通过缓存机制避免在异步上下文中直接访问 ORM。
应用启动时预加载缓存，后续调用优先走缓存。
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ─── 缓存 ───────────────────────────────────────────────────────────
_registry_cache: dict[str, dict[str, Any]] | None = None
_embedding_registry_cache: list[dict[str, Any]] | None = None


def invalidate_cache():
    """清除缓存，下次读取时重新从数据库加载"""
    global _registry_cache, _embedding_registry_cache
    _registry_cache = None
    _embedding_registry_cache = None


def warmup_cache():
    """预加载缓存，应在应用启动时（同步上下文）调用。

    Django AppConfig.ready() 是同步上下文，适合预加载。
    预加载后，异步上下文中的 get_model_registry() 直接走缓存，
    不再触发 ORM 调用，避免 "You cannot call this from an async context" 错误。
    """
    try:
        get_model_registry()
        get_embedding_registry()
        logger.debug("Registry 缓存预热完成")
    except Exception as e:
        logger.warning(f"Registry 缓存预热失败（非致命）: {e}")


def _is_async_context() -> bool:
    """检测当前是否在异步上下文中"""
    try:
        import asyncio
        return asyncio.get_running_loop() is not None
    except RuntimeError:
        return False


# ─── MODEL_REGISTRY 兼容接口 ────────────────────────────────────────

def get_model_registry() -> dict[str, dict[str, Any]]:
    """
    从数据库构建与 config.MODEL_REGISTRY 格式兼容的字典。

    异步安全：如果缓存已预热，直接返回缓存；如果处于异步上下文且缓存为空，
    回退到 config.py 而非触发 ORM（避免 SynchronousOnlyOperation 错误）。
    """
    global _registry_cache
    if _registry_cache is not None:
        return _registry_cache

    # 异步上下文中不能直接访问 ORM，回退到 config.py
    if _is_async_context():
        logger.debug("异步上下文中访问 registry，缓存未预热，回退到 config.py")
        from Django_xm.apps.ai_engine.config import MODEL_REGISTRY
        return MODEL_REGISTRY

    from Django_xm.apps.ai_engine.models import AIProvider

    registry: dict[str, dict[str, Any]] = {}
    try:
        providers = AIProvider.objects.filter(is_enabled=True).select_related().prefetch_related('models')
        for p in providers:
            enabled_models = p.models.filter(is_enabled=True).order_by('sort_order', 'name')
            # 构建模型列表：带 capabilities 的 dict 格式
            model_list = []
            model_names = []
            for m in enabled_models:
                model_list.append({
                    "name": m.name,
                    "capabilities": m.capabilities or [],
                })
                model_names.append(m.name)
            registry[p.provider_id] = {
                "label": p.label,
                "provider": p.provider,
                "icon": p.icon or "",
                "models": model_list,
                "model_names": model_names,  # 兼容：仅名称列表
                "default_model": p.default_model or (model_names[0] if model_names else ""),
                "api_key_attr": p.api_key_attr or None,
                "base_url_attr": p.base_url_attr or None,
                "special_params": p.special_params or {},
                "presets": p.presets or {},
            }
    except Exception as e:
        logger.warning(f"从数据库读取 MODEL_REGISTRY 失败，回退到 config.py: {e}")
        from Django_xm.apps.ai_engine.config import MODEL_REGISTRY
        return MODEL_REGISTRY

    if not registry:
        logger.warning("数据库中无 AIProvider 数据，回退到 config.py")
        from Django_xm.apps.ai_engine.config import MODEL_REGISTRY
        return MODEL_REGISTRY

    _registry_cache = registry
    return registry


def get_provider_config(provider_id: str) -> dict[str, Any]:
    """获取单个 provider 的配置，等价于 MODEL_REGISTRY.get(provider_id, {})"""
    return get_model_registry().get(provider_id, {})


def get_all_provider_ids() -> list[str]:
    """获取所有已启用的 provider_id 列表"""
    return list(get_model_registry().keys())


def get_provider_models(provider_id: str) -> list[str]:
    """获取某个 provider 下所有已启用的模型名列表"""
    cfg = get_provider_config(provider_id)
    # 优先用 model_names（纯名称列表），回退到 models 字段提取
    if "model_names" in cfg:
        return cfg["model_names"]
    return [m["name"] if isinstance(m, dict) else m for m in cfg.get("models", [])]


def get_provider_default_model(provider_id: str) -> str:
    """获取某个 provider 的默认模型名"""
    cfg = get_provider_config(provider_id)
    return cfg.get("default_model", "")


def is_provider_valid(provider_id: str) -> bool:
    """判断 provider_id 是否存在于注册表中"""
    return provider_id in get_model_registry()


# ─── EMBEDDING_PROVIDER_REGISTRY 兼容接口 ───────────────────────────

def get_embedding_registry() -> list[dict[str, Any]]:
    """
    从数据库构建与 embedding_factory.EMBEDDING_PROVIDER_REGISTRY 格式兼容的列表。

    异步安全：与 get_model_registry() 相同策略，异步上下文中回退到硬编码。
    """
    global _embedding_registry_cache
    if _embedding_registry_cache is not None:
        return _embedding_registry_cache

    # 异步上下文中不能直接访问 ORM
    if _is_async_context():
        logger.debug("异步上下文中访问 embedding registry，缓存未预热，回退到硬编码")
        from Django_xm.apps.ai_engine.services.embedding_factory import EMBEDDING_PROVIDER_REGISTRY
        return EMBEDDING_PROVIDER_REGISTRY

    from Django_xm.apps.ai_engine.models import EmbeddingProviderConfig

    registry: list[dict[str, Any]] = []
    try:
        providers = EmbeddingProviderConfig.objects.filter(is_enabled=True).select_related('provider').order_by('sort_order', 'id')
        for p in providers:
            # 同一 Provider 可有多个 Embedding 配置，id 用 "provider_id/model_name" 格式保证唯一
            pid = p.provider.provider_id if p.provider else "unknown"
            model_name = p.default_model or p.name or ""
            unique_id = f"{pid}/{model_name}" if model_name else pid
            registry.append({
                "id": unique_id,
                "provider_id": pid,
                "label": p.label,
                "default_model": p.default_model,
                "factory_path": p.factory_path,
                "key_attr": p.key_attr or None,
                "enabled": p.is_enabled,
                "supported_params": p.supported_params or [],
                "dimension": p.dimension,
                "native_max_dimension": p.native_max_dimension or 0,
                "min_dimension": p.min_dimension or 0,
            })
    except Exception as e:
        logger.warning(f"从数据库读取 EMBEDDING_PROVIDER_REGISTRY 失败，回退到硬编码: {e}")
        from Django_xm.apps.ai_engine.services.embedding_factory import EMBEDDING_PROVIDER_REGISTRY
        return EMBEDDING_PROVIDER_REGISTRY

    if not registry:
        logger.warning("数据库中无 EmbeddingProviderConfig 数据，回退到硬编码")
        from Django_xm.apps.ai_engine.services.embedding_factory import EMBEDDING_PROVIDER_REGISTRY
        return EMBEDDING_PROVIDER_REGISTRY

    _embedding_registry_cache = registry
    return registry


def get_embedding_provider_config(provider_id: str) -> dict[str, Any] | None:
    """获取单个 embedding provider 的配置"""
    for p in get_embedding_registry():
        if p["id"] == provider_id:
            return p
    return None


def get_embedding_provider_ids() -> list[str]:
    """获取所有已启用的 embedding provider_id 列表"""
    return [p["id"] for p in get_embedding_registry()]


# ─── 可用性检查 ─────────────────────────────────────────────────────

def is_provider_available(provider_id: str) -> bool:
    """
    判断 provider 是否可用（API key 已配置或为本地 provider）。
    与 llm_factory._is_provider_available 逻辑一致。
    """
    cfg = get_provider_config(provider_id)
    if not cfg:
        return False
    # 配置真相源是 ai_engine.config 的 Pydantic Settings（统一读取 .env / 环境变量）
    # 不是 django.conf.settings（后者只装载 Django 框架级配置）
    from Django_xm.apps.ai_engine.config import settings as app_cfg
    key_attr = cfg.get("api_key_attr")
    if key_attr is None:
        # 本地 provider：检查 base_url
        base_url_attr = cfg.get("base_url_attr")
        base_url = getattr(app_cfg, base_url_attr, "") if base_url_attr else ""
        return bool(base_url)
    api_key = getattr(app_cfg, key_attr, "")
    return bool(api_key and api_key.strip())


def get_available_providers() -> list[str]:
    """获取所有可用的 provider_id 列表（API key 已配置）"""
    return [pid for pid in get_all_provider_ids() if is_provider_available(pid)]
