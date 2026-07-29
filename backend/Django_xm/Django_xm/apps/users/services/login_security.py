"""登录安全服务：按 username 失败锁定。

Redis 计数 + TTL，5 次/h 失败锁定 1 小时。

Key 设计：
    - 失败计数: ``login_fail:{username}``，TTL 1 小时（从首次失败开始计时的固定窗口）
    - 锁定标记: ``login_lock:{username}``，TTL 1 小时

策略：
    - 首次失败时 ``cache.set(key, 1, 3600)`` 设置计数与 TTL；
    - 后续失败 ``cache.incr(key)`` 原子递增，不重置 TTL（保持首次失败时刻的 1h 窗口）；
    - 达到 ``MAX_FAIL_COUNT`` 时设置锁定 key；
    - 锁定期间直接拒绝登录（即使密码正确）；
    - 登录成功立即清除失败计数（避免历史失败影响）。

为何使用固定窗口而非滑动窗口：
    - 登录失败计数场景下，固定窗口实现简单且语义清晰（"首次失败后 1 小时内 5 次失败即锁定"）；
    - 滑动窗口需要 Redis ZSET，开销大且对登录场景收益有限。
"""

import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)


class LoginSecurityService:
    """登录失败锁定服务（按 username 维度）。"""

    MAX_FAIL_COUNT = 5
    FAIL_COUNT_TTL = 3600  # 失败计数窗口，1 小时（秒）
    LOCK_TTL = 3600  # 锁定时长，1 小时（秒）

    _FAIL_KEY_PREFIX = "login_fail:"
    _LOCK_KEY_PREFIX = "login_lock:"

    @classmethod
    def _fail_key(cls, username: str) -> str:
        return f"{cls._FAIL_KEY_PREFIX}{username}"

    @classmethod
    def _lock_key(cls, username: str) -> str:
        return f"{cls._LOCK_KEY_PREFIX}{username}"

    @classmethod
    def is_locked(cls, username: str) -> bool:
        """检查账号是否处于锁定状态。

        Args:
            username: 用户名。

        Returns:
            True 表示账号已被锁定，应拒绝登录。
        """
        if not username:
            return False
        return bool(cache.get(cls._lock_key(username)))

    @classmethod
    def get_remaining_lock_seconds(cls, username: str) -> int:
        """获取账号剩余锁定时间（秒）。

        用于响应中提示用户剩余等待时长。
        """
        if not username:
            return 0
        # django-redis 扩展方法：BaseCache 未声明 ttl()，django-stubs 不覆盖。
        # 返回值约定：None(key 不存在) / -1(无过期) / >=0(剩余秒数)。
        ttl = cache.ttl(cls._lock_key(username))  # type: ignore[attr-defined]  # django-redis extension
        if ttl is None or ttl < 0:
            return 0
        return int(ttl)

    @classmethod
    def record_failure(cls, username: str) -> int:
        """记录一次登录失败。

        达到 ``MAX_FAIL_COUNT`` 阈值时设置锁定 key。

        Args:
            username: 用户名。

        Returns:
            当前失败次数（0 表示 username 为空，不计数）。
        """
        if not username:
            return 0
        key = cls._fail_key(username)
        try:
            count = cache.incr(key)
        except ValueError:
            # 首次失败：初始化 key + TTL（后续 incr 不重置 TTL，保持首次失败时刻的 1h 窗口）
            cache.set(key, 1, cls.FAIL_COUNT_TTL)
            count = 1

        if count >= cls.MAX_FAIL_COUNT:
            cache.set(cls._lock_key(username), 1, cls.LOCK_TTL)
            logger.warning(
                f"[LoginSecurity] 账号锁定: username={username}, fail_count={count}, lock_ttl={cls.LOCK_TTL}s"
            )
        else:
            logger.info(f"[LoginSecurity] 登录失败计数: username={username}, fail_count={count}/{cls.MAX_FAIL_COUNT}")
        return count

    @classmethod
    def record_success(cls, username: str) -> None:
        """登录成功，清除失败计数。

        锁定状态下不应调用本方法（锁定时直接拒绝登录，不会到达成功分支）。
        """
        if not username:
            return
        cache.delete(cls._fail_key(username))
