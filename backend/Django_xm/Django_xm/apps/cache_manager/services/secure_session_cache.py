import logging

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)


class SecureSessionCacheService:
    CACHE_PREFIX = "user_session"
    SESSION_LIST_KEY = "user_sessions_list"
    TIMEOUT = 3600

    @classmethod
    def _get_cache_key(cls, user_id, session_id):
        return f"{cls.CACHE_PREFIX}:{user_id}:{session_id}"

    @classmethod
    def _get_user_sessions_key(cls, user_id):
        return f"{cls.SESSION_LIST_KEY}:{user_id}"

    @classmethod
    def cache_session(cls, user_id, session_data):
        try:
            session_id = session_data.get("session_id")
            if not session_id:
                logger.error(f"Session data missing session_id for user {user_id}")
                return False

            cache_key = cls._get_cache_key(user_id, session_id)

            serialized_data = {"user_id": user_id, "cached_at": timezone.now().isoformat(), **session_data}

            cache.set(cache_key, serialized_data, timeout=cls.TIMEOUT)

            sessions_list_key = cls._get_user_sessions_key(user_id)
            existing_sessions = cache.get(sessions_list_key, [])

            if session_id not in existing_sessions:
                existing_sessions.append(session_id)
                cache.set(sessions_list_key, existing_sessions, timeout=cls.TIMEOUT * 24)

            logger.debug(f"Cached session {session_id} for user {user_id}")
            return True

        except Exception:
            logger.exception(f"Failed to cache session for user {user_id}")
            return False

    @classmethod
    def get_cached_sessions_batch(cls, user_id, session_ids):
        """批量读取缓存，避免逐个读取的 N+1 问题"""
        if not session_ids:
            return []
        try:
            cache_keys = [cls._get_cache_key(user_id, sid) for sid in session_ids]
            cached_data = cache.get_many(cache_keys)
            results = []
            for key in cache_keys:
                data = cached_data.get(key)
                if data is not None:
                    results.append(data)
            return results
        except Exception:
            logger.exception("Failed to batch get cached sessions")
            return []

    @classmethod
    def get_cached_session(cls, user_id, session_id):
        try:
            cache_key = cls._get_cache_key(user_id, session_id)
            session_data = cache.get(cache_key)

            if session_data:
                logger.debug(f"Cache hit for session {session_id} of user {user_id}")
                return session_data

            logger.debug(f"Cache miss for session {session_id} of user {user_id}")
            return None

        except Exception:
            logger.exception("Failed to get cached session")
            return None

    @classmethod
    def get_user_sessions_list(cls, user_id):
        try:
            sessions_list_key = cls._get_user_sessions_key(user_id)
            return cache.get(sessions_list_key, [])
        except Exception:
            logger.exception("Failed to get user sessions list")
            return []

    @classmethod
    def invalidate_user_session(cls, user_id, session_id):
        try:
            cache_key = cls._get_cache_key(user_id, session_id)
            cache.delete(cache_key)

            sessions_list_key = cls._get_user_sessions_key(user_id)
            existing_sessions = cache.get(sessions_list_key, [])

            if session_id in existing_sessions:
                existing_sessions.remove(session_id)
                cache.set(sessions_list_key, existing_sessions, timeout=cls.TIMEOUT * 24)

            logger.info(f"Invalidated session {session_id} for user {user_id}")
            return True

        except Exception:
            logger.exception("Failed to invalidate session")
            return False

    @classmethod
    def invalidate_all_user_sessions(cls, user_id):
        try:
            session_ids = cls.get_user_sessions_list(user_id)
            invalidated_count = 0

            if session_ids:
                cache_keys = [cls._get_cache_key(user_id, sid) for sid in session_ids]
                cache.delete_many(cache_keys)
                invalidated_count = len(session_ids)

            sessions_list_key = cls._get_user_sessions_key(user_id)
            cache.delete(sessions_list_key)

            logger.info(f"Invalidated all {invalidated_count} sessions for user {user_id}")
            return invalidated_count

        except Exception:
            logger.exception(f"Failed to invalidate all sessions for user {user_id}")
            return 0

    @classmethod
    def cache_messages_for_session(cls, user_id, session_id, messages):
        try:
            messages_key = f"{cls._get_cache_key(user_id, session_id)}:messages"
            cache.set(messages_key, messages, timeout=cls.TIMEOUT // 2)
            return True
        except Exception:
            logger.exception("Failed to cache messages")
            return False

    @classmethod
    def get_cached_messages(cls, user_id, session_id):
        try:
            messages_key = f"{cls._get_cache_key(user_id, session_id)}:messages"
            return cache.get(messages_key)
        except Exception:
            logger.exception("Failed to get cached messages")
            return None

    @classmethod
    def update_session_access_time(cls, user_id, session_id):
        try:
            cache_key = cls._get_cache_key(user_id, session_id)
            session_data = cache.get(cache_key)

            if session_data:
                session_data["last_accessed"] = timezone.now().isoformat()
                cache.set(cache_key, session_data, timeout=cls.TIMEOUT)

        except Exception:
            logger.exception("Failed to update access time")
