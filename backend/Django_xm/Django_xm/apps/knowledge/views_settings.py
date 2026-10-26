"""
AI 设置 API（知识库/索引相关面）

原位于 ai_engine/settings_views.py（任务 eliminate-priority-debts 4.2：环 2
{ai_engine, knowledge} 破除——视图组纯迁移至 knowledge app，视图逻辑零变化）。
对外 URL 路径保持不变（/api/v1/ai-engine/settings(/rebuild-indexes)/，在根
urls.py 直挂），前端零改动。

提供系统级 AI 模型配置的读取、更新和索引重建功能。
配置持久化到 SystemConfig 数据库表，服务重启后不丢失。
"""

import logging
from typing import Any

from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAdminUser
from rest_framework.views import APIView

from Django_xm.apps.ai_engine.config import HELPER_MODEL_PRIORITY, get_available_providers
from Django_xm.apps.ai_engine.models import SystemConfig
from Django_xm.apps.ai_engine.services.embedding_factory import (
    get_embedding_fallback_chain,
)
from Django_xm.apps.ai_engine.services.registry_service import (
    get_embedding_provider_ids,
    get_embedding_registry,
    get_model_registry,
    is_provider_valid,
)
from Django_xm.apps.cache_manager.services.cache_service import CacheService, invalidate_knowledge_cache
from Django_xm.apps.core.throttling import MetaRateThrottle
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.exceptions import BaseAppError
from Django_xm.common.responses import success_response

from .rag_settings import get_rag_runtime_settings, save_rag_runtime_settings
from .services.cross_app import get_index_manager

logger = logging.getLogger(__name__)


def _get_embedding_providers():
    """获取可用的 Embedding provider 列表"""
    available = get_embedding_fallback_chain()
    result = []
    for cfg in available:
        result.append(
            {
                "id": cfg["id"],
                "provider_id": cfg.get("provider_id", cfg["id"].split("/")[0] if "/" in cfg["id"] else cfg["id"]),
                "label": cfg["label"],
                "default_model": cfg.get("default_model", ""),
                "dimension": cfg.get("dimension"),
                "native_max_dimension": cfg.get("native_max_dimension", 0),
                "min_dimension": cfg.get("min_dimension", 0),
            }
        )
    return result


def _get_affected_indexes(user, new_dimension: int) -> list:
    """获取维度不匹配的索引列表"""
    from Django_xm.apps.knowledge.services.embedding_service import get_embedding_dimension

    manager = get_index_manager()
    indexes = manager.list_indexes()
    affected = []
    for idx in indexes:
        name = idx.get("name", "")
        idx_dim = idx.get("embedding_dimension")
        num_docs = idx.get("num_documents", 0)
        if num_docs == 0:
            continue

        # 旧索引可能没有 embedding_dimension，从 embedding_model 推断
        if idx_dim is None:
            emb_model = idx.get("embedding_model", "")
            if emb_model:
                idx_dim = get_embedding_dimension(emb_model)

        # 维度不匹配
        if idx_dim is not None and idx_dim != new_dimension:
            affected.append(
                {
                    "name": name,
                    "current_dimension": idx_dim,
                    "new_dimension": new_dimension,
                    "num_documents": num_docs,
                }
            )
    return affected


class AISettingsView(APIView):
    """全局 AI 设置视图

    仅管理员可访问（IsAdminUser），普通用户访问返回 403。
    涉及系统级 LLM/Embedding 配置，属于敏感操作。
    """

    permission_classes = [IsAdminUser]
    # 页面加载即请求的只读接口，独立 meta 额度（Task 3.2）
    throttle_classes = [MetaRateThrottle]

    @extend_schema(exclude=True)
    def get(self, request):
        """获取全部 AI 设置"""
        # 可用 LLM provider 列表
        providers = get_available_providers()

        # 可用 Embedding provider 列表
        embedding_providers = _get_embedding_providers()

        # 从数据库读取当前配置
        chat_config = SystemConfig.get_value("default_chat_model", {})
        helper_config = SystemConfig.get_value("helper_model", {})
        embedding_config = SystemConfig.get_value("embedding_provider", {})
        fallback_chat_config = SystemConfig.get_value("fallback_chat_model", {})
        fallback_embedding_config = SystemConfig.get_value("fallback_embedding_provider", {})

        # 运行时内存中的辅助模型配置（兼容旧逻辑）
        from django.conf import settings as django_settings

        runtime_helper_provider = getattr(django_settings, "AI_HELPER_MODEL_PROVIDER", "")
        runtime_helper_model = getattr(django_settings, "AI_HELPER_MODEL_NAME", "")

        # 合并：数据库优先，回退到运行时内存
        current_chat = {
            "provider_id": chat_config.get("provider_id", "") or "",
            "model_name": chat_config.get("model_name", "") or "",
        }
        current_helper = {
            "provider_id": helper_config.get("provider_id", "") or runtime_helper_provider or "",
            "model_name": helper_config.get("model_name", "") or runtime_helper_model or "",
            "is_auto": not (helper_config.get("provider_id") or runtime_helper_provider),
        }
        current_embedding = {
            "provider_id": embedding_config.get("provider_id", "") or "",
            "dimension": embedding_config.get("dimension", None),  # MRL 截断维度（前端可改）
        }
        current_fallback_chat = {
            "provider_id": fallback_chat_config.get("provider_id", "") or "",
            "model_name": fallback_chat_config.get("model_name", "") or "",
        }
        current_fallback_embedding = {
            "provider_id": fallback_embedding_config.get("provider_id", "") or "",
        }

        # 辅助模型优先级列表
        helper_priority = []
        for item in HELPER_MODEL_PRIORITY:
            pid = item["provider"]
            provider_cfg = get_model_registry().get(pid, {})
            helper_priority.append(
                {
                    "provider_id": pid,
                    "model_name": item["model"],
                    "reason": item["reason"],
                    "label": provider_cfg.get("label", pid),
                }
            )

        return success_response(
            data={
                "providers": providers,
                "embedding_providers": embedding_providers,
                "current": {
                    "default_chat_model": current_chat,
                    "fallback_chat_model": current_fallback_chat,
                    "helper_model": current_helper,
                    "embedding_provider": current_embedding,
                    "fallback_embedding_provider": current_fallback_embedding,
                },
                "indexes": self._get_index_dimensions(),
                "rag_retrieval": get_rag_runtime_settings(),
            }
        )

    def _get_index_dimensions(self) -> list:
        """获取所有索引的维度信息，供前端预检测"""
        from Django_xm.apps.knowledge.services.embedding_service import get_embedding_dimension

        manager = get_index_manager()
        indexes = manager.list_indexes()
        result = []
        for idx in indexes:
            num_docs = idx.get("num_documents", 0)
            if num_docs == 0:
                continue
            idx_dim = idx.get("embedding_dimension")
            if idx_dim is None:
                emb_model = idx.get("embedding_model", "")
                if emb_model:
                    idx_dim = get_embedding_dimension(emb_model)
            result.append(
                {
                    "name": idx.get("name", ""),
                    "embedding_dimension": idx_dim,
                    "embedding_model": idx.get("embedding_model", ""),
                    "num_documents": num_docs,
                }
            )
        return result

    @extend_schema(exclude=True)
    def put(self, request):
        """更新 AI 设置

        接收:
        - default_chat_model: {"provider_id": "...", "model_name": "..."}
        - fallback_chat_model: {"provider_id": "...", "model_name": "..."}
        - helper_model: {"provider_id": "...", "model_name": "..."}
        - embedding_provider: {"provider_id": "..."}
        - fallback_embedding_provider: {"provider_id": "..."}
        - rag_retrieval: {use_reranker/hybrid_enabled/...}
        """
        user = request.user
        chat_model = request.data.get("default_chat_model")
        fallback_chat = request.data.get("fallback_chat_model")
        helper_model = request.data.get("helper_model")
        embedding_provider = request.data.get("embedding_provider")
        fallback_embedding = request.data.get("fallback_embedding_provider")
        rag_retrieval = request.data.get("rag_retrieval")

        dimension_info = None
        rag_updated = None

        # 0. 保存 RAG 检索增强设置（白名单字段 + 范围归一，即时生效）
        if rag_retrieval is not None:
            rag_updated = save_rag_runtime_settings(rag_retrieval)

        # 1. 保存默认聊天模型
        if chat_model is not None:
            provider_id = chat_model.get("provider_id", "")
            model_name = chat_model.get("model_name", "")
            if provider_id and not is_provider_valid(provider_id):
                raise BaseAppError(f"未知的 LLM provider: {provider_id}", business_code=ErrorCode.INVALID_PARAMS)
            if provider_id:
                SystemConfig.set_value(
                    "default_chat_model",
                    {
                        "provider_id": provider_id,
                        "model_name": model_name,
                    },
                )
            else:
                SystemConfig.set_value("default_chat_model", None)

        # 2. 保存辅助模型
        if helper_model is not None:
            provider_id = helper_model.get("provider_id", "")
            model_name = helper_model.get("model_name", "")
            if provider_id and not is_provider_valid(provider_id):
                raise BaseAppError(f"未知的 LLM provider: {provider_id}", business_code=ErrorCode.INVALID_PARAMS)
            # 空字符串视为重置，存 null
            if provider_id:
                SystemConfig.set_value(
                    "helper_model",
                    {
                        "provider_id": provider_id,
                        "model_name": model_name,
                    },
                )
            else:
                SystemConfig.set_value("helper_model", None)
            # 同步运行时内存（get_helper_model 每次调用即时构建，无需复位缓存）
            from django.conf import settings as django_settings

            django_settings.AI_HELPER_MODEL_PROVIDER = provider_id
            django_settings.AI_HELPER_MODEL_NAME = model_name

        # 3. 保存降级聊天模型
        if fallback_chat is not None:
            fb_provider_id = fallback_chat.get("provider_id", "")
            fb_model_name = fallback_chat.get("model_name", "")
            if fb_provider_id and not is_provider_valid(fb_provider_id):
                raise BaseAppError(f"未知的 LLM provider: {fb_provider_id}", business_code=ErrorCode.INVALID_PARAMS)
            if fb_provider_id:
                SystemConfig.set_value(
                    "fallback_chat_model",
                    {
                        "provider_id": fb_provider_id,
                        "model_name": fb_model_name,
                    },
                )
            else:
                SystemConfig.set_value("fallback_chat_model", None)

        # 4. 保存 Embedding provider
        if embedding_provider is not None:
            new_provider_id = embedding_provider.get("provider_id", "")
            new_dimension = embedding_provider.get("dimension", None)

            # 验证 provider 是否合法
            if new_provider_id:
                valid_ids = set(get_embedding_provider_ids())
                if new_provider_id not in valid_ids:
                    raise BaseAppError(
                        f"未知的 Embedding provider: {new_provider_id}",
                        business_code=ErrorCode.INVALID_PARAMS,
                    )

            # 获取旧配置
            old_config = SystemConfig.get_value("embedding_provider", {})
            old_provider_id = old_config.get("provider_id", "")
            old_dimension = old_config.get("dimension", None)

            # 校验 dimension（MRL 模型允许在前端调整）
            new_cfg: dict[str, Any] = {"provider_id": new_provider_id} if new_provider_id else {}
            if new_provider_id and new_dimension is not None:
                try:
                    new_dimension = int(new_dimension)
                except (TypeError, ValueError) as e:
                    raise BaseAppError("dimension 必须是整数", business_code=ErrorCode.INVALID_PARAMS) from e
                # 找到 provider 配置，校验范围
                provider_cfg = None
                for p in get_embedding_registry():
                    if p["id"] == new_provider_id:
                        provider_cfg = p
                        break
                if provider_cfg is None:
                    raise BaseAppError(
                        f"未找到 Embedding provider 配置: {new_provider_id}",
                        business_code=ErrorCode.INVALID_PARAMS,
                    )
                # MRL 截断校验：min_dimension > 0 且 native_max_dimension > 0 表示支持 MRL
                # 范围：[min_dimension, native_max_dimension] 之间任意整数
                native_max = provider_cfg.get("native_max_dimension") or 0
                min_dim = provider_cfg.get("min_dimension") or 0

                if native_max > 0 and min_dim > 0:
                    if new_dimension < min_dim or new_dimension > native_max:
                        raise BaseAppError(
                            f"dimension 必须在 {min_dim}~{native_max} 范围内",
                            business_code=ErrorCode.INVALID_PARAMS,
                        )
                else:
                    raise BaseAppError(
                        "此 Embedding 不支持 MRL 截断（min_dimension 或 native_max_dimension 未配置）",
                        business_code=ErrorCode.INVALID_PARAMS,
                    )
                # 等于 native_max 时清空（表示不截断）
                if native_max and new_dimension == native_max:
                    new_cfg["dimension"] = None
                else:
                    new_cfg["dimension"] = new_dimension

            if new_cfg:
                SystemConfig.set_value("embedding_provider", new_cfg)
            else:
                SystemConfig.set_value("embedding_provider", None)

            # 实例缓存已退役（get_embeddings_with_fallback 每次调用即时构建），
            # 新配置天然即时生效，无需复位缓存

            # 检测维度变化：provider 切换 OR dimension 修改
            dimension_changed = new_provider_id != old_provider_id or new_cfg.get("dimension") != old_dimension
            if new_provider_id and dimension_changed:
                # RP-06：纵深防御——查询缓存键已含活跃 provider id/维度段（键级隔离），
                # 此处再整体清存量 embedding 响应缓存，防止旧键残留至 TTL 过期
                #（provider 不变、仅模型更新的场景键无感知，依赖此清理兜底）
                CacheService.delete_pattern("embedding:*")

                # 获取新 provider 的有效输出维度
                new_effective_dim = None
                for p in get_embedding_registry():
                    if p["id"] == new_provider_id:
                        new_effective_dim = new_cfg.get("dimension") if new_cfg.get("dimension") else p.get("dimension")
                        break

                if new_effective_dim:
                    affected = _get_affected_indexes(user, new_effective_dim)
                    if affected:
                        dimension_info = {
                            "dimension_changed": True,
                            "old_provider": old_provider_id,
                            "new_provider": new_provider_id,
                            "new_dimension": new_effective_dim,
                            "affected_indexes": affected,
                        }

        # 5. 保存降级 Embedding provider
        if fallback_embedding is not None:
            fb_emb_id = fallback_embedding.get("provider_id", "")
            if fb_emb_id:
                valid_ids = set(get_embedding_provider_ids())
                if fb_emb_id not in valid_ids:
                    raise BaseAppError(
                        f"未知的 Embedding provider: {fb_emb_id}",
                        business_code=ErrorCode.INVALID_PARAMS,
                    )
                SystemConfig.set_value(
                    "fallback_embedding_provider",
                    {
                        "provider_id": fb_emb_id,
                    },
                )
            else:
                SystemConfig.set_value("fallback_embedding_provider", None)

        return success_response(
            data={
                "dimension_info": dimension_info,
                "rag_retrieval": rag_updated or get_rag_runtime_settings(),
            },
            message="设置已保存",
        )


class RebuildIndexesView(APIView):
    """触发索引重建视图

    仅管理员可访问（IsAdminUser），普通用户访问返回 403。
    重建索引会调整 PGVector 维度，属于高风险操作。
    """

    permission_classes = [IsAdminUser]

    @extend_schema(exclude=True)
    def post(self, request):
        """触发索引重建

        接收:
        - provider_id: 使用哪个 embedding provider 重建
        - index_names: 可选，指定要重建的索引名列表；为空则重建所有维度不匹配的索引
        """
        user = request.user
        provider_id = request.data.get("provider_id", "")
        index_names = request.data.get("index_names", [])

        if not provider_id:
            raise BaseAppError("provider_id 不能为空", business_code=ErrorCode.INVALID_PARAMS)

        # 验证 provider
        valid_ids = set(get_embedding_provider_ids())
        if provider_id not in valid_ids:
            raise BaseAppError(f"未知的 Embedding provider: {provider_id}", business_code=ErrorCode.INVALID_PARAMS)

        # 获取新维度
        new_dimension = None
        for p in get_embedding_registry():
            if p["id"] == provider_id:
                new_dimension = p.get("dimension")
                break

        # 确定要重建的索引
        manager = get_index_manager()
        all_indexes = manager.list_indexes()

        if index_names:
            # 用户指定了索引
            targets = [idx for idx in all_indexes if idx.get("name") in index_names]
        # 重建所有维度不匹配的索引
        elif new_dimension:
            targets = [
                idx
                for idx in all_indexes
                if idx.get("num_documents", 0) > 0
                and idx.get("embedding_dimension") is not None
                and idx.get("embedding_dimension") != new_dimension
            ]
        else:
            targets = []

        if not targets:
            return success_response(
                data={"rebuilt_indexes": [], "total": 0},
                message="无需重建的索引",
            )

        # 逐个索引安全重建：先读取文档 → 删除旧索引 → 创建新索引
        # 如果创建失败，至少文档已保存在内存中，可以重试
        rebuilt = []
        errors = []
        from Django_xm.apps.knowledge.services.embedding_service import get_embeddings

        # 预先读取所有索引的文档（在删除前读取，避免中间状态丢数据）
        index_docs = {}  # {name: (documents, store_type, description)}
        for idx in targets:
            name = idx.get("name", "")
            try:
                from langchain_core.documents import Document

                documents = []

                # 管理员系统级重建：user_id=None（IndexManager 可信内部路径，
                # RebuildIndexesView 为 IsAdminUser，跨用户全量索引遍历属设计语义）
                store_type = idx.get("store_type") or manager.get_store_type(name, user_id=None) or "pgvector"

                if store_type == "pgvector":
                    # 通过 PGVectorBackend 封装层读取文档，避免直接写原生 SQL（Task 20.1）
                    backend = manager.get_backend("pgvector")
                    documents = backend.read_all_documents(name)
                else:
                    old_dim = idx.get("embedding_dimension")
                    old_embeddings = get_embeddings(
                        required_dimension=old_dim,
                        use_cache=False,
                    )
                    old_store = manager.load_index(name, old_embeddings)
                    if hasattr(old_store, "docstore") and hasattr(old_store, "index_to_docstore_id"):
                        for doc_id in old_store.index_to_docstore_id.values():
                            doc = old_store.docstore.search(doc_id)
                            if isinstance(doc, Document):
                                documents.append(doc)

                description = (manager.load_metadata(name, user_id=None) or {}).get("description", "")
                if documents:
                    index_docs[name] = (documents, store_type, description)
                    logger.info(f"索引文档读取成功: {name}, {len(documents)} 条")
                else:
                    errors.append({"name": name, "error": "无法读取文档"})
            except Exception as e:
                logger.exception(f"索引文档读取失败: {name} -")
                errors.append({"name": name, "error": f"读取失败: {e}"})

        # 所有文档读取完成后，逐个重建索引
        # 安全策略：先建新索引（overwrite=True），成功后再删旧索引
        # 避免建新失败时旧索引也被删除导致数据丢失
        new_embeddings = get_embeddings(
            preferred_provider=provider_id,
            use_cache=False,
        )

        # 维度变化时，PGVector 需要先删除所有旧集合数据并调整 embedding 列类型
        # 因为 PGVector 的 embedding 列维度是表级的，所有集合共享
        dimension_changed = False
        if new_dimension:
            from Django_xm.apps.knowledge.vector_store.pgvector_backend import PGVectorBackend

            backend = PGVectorBackend()
            current_dim = backend.get_embedding_column_dimension()
            if current_dim is not None and current_dim != new_dimension:
                dimension_changed = True
                logger.info(f"检测到维度变化: {current_dim} → {new_dimension}，先删除所有旧集合数据并调整列类型")
                # 先删除所有要重建的集合的旧数据
                for name in index_docs:
                    try:
                        backend.delete(name)
                        logger.info(f"维度重建：已删除旧集合 {name}")
                    except Exception as e:
                        logger.warning(f"维度重建：删除旧集合 {name} 失败: {e}")
                # 调整 embedding 列维度（A-3：清理回调注入——维度调整后 pgvector
                # 经回调取 IndexManager 缓存快照并 dispose 旧 engine 连接池，
                # C3 红线：清缓存必须 dispose，禁止"丢弃不 dispose"路径）
                dim_ok = backend.ensure_embedding_dimension(
                    new_dimension, cache_clear_callback=manager.clear_cache
                )
                if not dim_ok:
                    errors.insert(
                        0,
                        {
                            "name": "__dimension__",
                            "error": f"PGVector embedding 列维度无法从 {current_dim} 调整为 {new_dimension}，"
                            f"表中仍有其他集合数据。请先删除所有知识库索引后再重建。",
                        },
                    )
                    return success_response(
                        data={
                            "rebuilt_indexes": [],
                            "errors": errors,
                            "total": 0,
                        },
                        message="重建失败: PGVector 维度不兼容",
                    )

        for name, (documents, store_type, description) in index_docs.items():
            try:
                # 维度变化时旧集合已被删除，不需要 overwrite；否则用 overwrite 保留内部备份恢复机制
                manager.create_index(
                    name=name,
                    documents=documents,
                    embeddings=new_embeddings,
                    description=description,
                    store_type=store_type,
                    overwrite=not dimension_changed,
                )
                rebuilt.append(name)
                # RP-07：重建后失效两层查询缓存（rag_query:*/vector_search:* 残留
                # 旧 provider 时代的答案与检索结果；对齐 kb_service 重建路径的失效语义）
                invalidate_knowledge_cache(user_id=user.id, user_index_name=name)
                logger.info(f"索引重建成功: {name}, {len(documents)} 条文档")
            except Exception:
                logger.exception(f"索引重建失败，尝试重试: {name} -")
                # 阶段4-1：用内存中的文档重试
                try:
                    manager.create_index(
                        name=name,
                        documents=documents,
                        embeddings=new_embeddings,
                        description=description,
                        store_type=store_type,
                        overwrite=False,
                    )
                    rebuilt.append(name)
                    logger.info(f"索引重建重试成功: {name}")
                except Exception:
                    # 阶段4-2：从原始文件重建
                    logger.exception(f"索引重建重试失败，尝试从原始文件重建: {name}")
                    try:
                        from Django_xm.apps.knowledge.services.kb_service import rebuild_index_from_source_files

                        rebuild_index_from_source_files(
                            user=user,
                            kb_name=name,
                            provider_id=provider_id,
                            embeddings=new_embeddings,
                        )
                        rebuilt.append(name)
                        logger.info(f"从原始文件重建成功: {name}")
                    except Exception as src_err:
                        logger.exception(f"从原始文件重建也失败: {name} -")
                        errors.append({"name": name, "error": f"重建失败: {src_err}"})

        return success_response(
            data={
                "rebuilt_indexes": rebuilt,
                "errors": errors,
                "total": len(rebuilt),
            },
            message=f"重建完成: {len(rebuilt)} 个索引成功" + (f", {len(errors)} 个失败" if errors else ""),
        )
