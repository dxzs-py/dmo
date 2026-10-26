"""会话附件索引（``attach_*``）TTL 清理：管理命令与 Celery beat 共用单一实现。

背景：附件检索索引是会话级 PG collection，会话删除/附件删除时会即时清理；
但“会话仍在、附件早已删除、索引却被重建过”等场景仍会留下孤立 collection，
需要按 TTL（``rag_attachment_ttl_days``，设置页可改）定期回收。

清理口径（与 ``RagService.delete_scope`` 一致）：
- 删除向量 collection + ``IndexMetadata`` 实体行 + BM25/检索结果缓存；
- 只处理名字以 ``rag_attachment_collection_prefix`` 开头且 ``updated_at`` 超期的索引；
- ``dry_run=True`` 只统计不删除（管理命令默认预览用）。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.utils import timezone

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)

DEFAULT_ATTACHMENT_COLLECTION_PREFIX = "attach_"


def cleanup_expired_attachment_indexes(
    *,
    days: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """清理超期的会话附件索引。

    Args:
        days: 覆盖 TTL 天数；None 时读运行时设置 ``attachment_ttl_days``
        dry_run: 只统计不删除

    Returns:
        ``{"days": int, "prefix": str, "scanned": int, "deleted": int, "failed": int,
        "dry_run": bool, "names": [...]}``
    """
    from Django_xm.apps.knowledge.models import IndexMetadata
    from Django_xm.apps.knowledge.rag_settings import get_rag_runtime_settings
    from Django_xm.apps.knowledge.services.rag_service import RagScope, RagService

    runtime = get_rag_runtime_settings()
    ttl_days = int(days if days is not None else runtime["attachment_ttl_days"])
    ttl_days = max(ttl_days, 1)
    prefix = (runtime.get("attachment_collection_prefix") or "").strip()
    # 前缀为空时回退默认值：空前缀会匹配到知识库索引（user_*），绝不能发生
    prefix = prefix or DEFAULT_ATTACHMENT_COLLECTION_PREFIX

    cutoff = timezone.now() - timedelta(days=ttl_days)
    candidates = list(
        IndexMetadata.objects.filter(name__startswith=prefix, updated_at__lt=cutoff).values_list(
            "name", flat=True
        )
    )

    result: dict[str, Any] = {
        "days": ttl_days,
        "prefix": prefix,
        "scanned": len(candidates),
        "deleted": 0,
        "failed": 0,
        "dry_run": dry_run,
        "names": candidates,
    }
    if dry_run or not candidates:
        logger.info(
            f"[AttachmentIndexCleanup] 扫描完成: prefix={prefix}, days={ttl_days}, "
            f"scanned={len(candidates)}, dry_run={dry_run}"
        )
        return result

    service = RagService()
    for name in candidates:
        try:
            service.delete_scope(RagScope("session_attachment", name))
            result["deleted"] += 1
        except Exception as e:
            result["failed"] += 1
            logger.warning(f"[AttachmentIndexCleanup] 删除失败 {name}: {e}")

    logger.info(
        f"[AttachmentIndexCleanup] 清理完成: prefix={prefix}, days={ttl_days}, "
        f"scanned={result['scanned']}, deleted={result['deleted']}, failed={result['failed']}"
    )
    return result
