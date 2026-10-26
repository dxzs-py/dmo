"""启动期身份自检：把 SECRET_KEY 指纹写进各自日志。

背景：channel layer 曾用 SECRET_KEY 做通道消息的对称加密，Django(8000) 与
FastAPI(8001) 一旦密钥不一致，接收端会抛 ``InvalidToken`` 打死 WebSocket 连接。
即使已移除该加密，SECRET_KEY 仍参与 JWT 签名——进程间不一致会导致
「A 进程签发的 token 在 B 进程无效」这类隐蔽故障。

把指纹写进 django.log / fastapi.log 后，只要两个进程的 fingerprint 不同，
一眼就能看出来，不必再靠猜。
"""

from __future__ import annotations

import hashlib
import logging

logger = logging.getLogger(__name__)


def secret_key_fingerprint() -> str:
    """返回 SECRET_KEY 的短指纹（仅用于排查，不可逆推原文）。"""
    from django.conf import settings

    secret = getattr(settings, "SECRET_KEY", "") or ""
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]
    return f"{digest}(len={len(secret)})"


def log_startup_identity(role: str) -> None:
    """记录进程角色 + SECRET_KEY 指纹，便于跨进程一致性核对。

    Args:
        role: 进程角色标识（web / fastapi / celery-worker / celery-beat）
    """
    from django.conf import settings

    try:
        fingerprint = secret_key_fingerprint()
    except Exception:  # pragma: no cover - settings 未就绪时不影响启动
        fingerprint = "unavailable"
    logger.info(
        "[Startup] role=%s settings=%s SECRET_KEY fingerprint=%s",
        role,
        getattr(settings, "SETTINGS_MODULE", "?"),
        fingerprint,
    )
