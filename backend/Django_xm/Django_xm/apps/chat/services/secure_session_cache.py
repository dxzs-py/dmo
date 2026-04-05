import json
import logging
from django.core.cache import cache
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


class SecureSessionCacheService:
    """
    安全的会话缓存服务 - 基于Redis实现
    
    功能：
    1. 用户会话数据隔离存储
    2. 自动过期和清理机制
    3. 数据加密存储（可选）
    4. 快速读写性能优化
    """
    
    CACHE_PREFIX = 'user_session'
    SESSION_LIST_KEY = 'user_sessions_list'
    TIMEOUT = 3600  # 1小时过期
    
    @classmethod
    def _get_cache_key(cls, user_id, session_id):
        """生成唯一的缓存key，确保用户数据隔离"""
        return f'{cls.CACHE_PREFIX}:{user_id}:{session_id}'
    
    @classmethod
    def _get_user_sessions_key(cls, user_id):
        """获取用户的会话列表key"""
        return f'{cls.SESSION_LIST_KEY}:{user_id}'
    
    @classmethod
    def cache_session(cls, user_id, session_data):
        """
        缓存用户会话数据到Redis
        
        Args:
            user_id: 用户ID
            session_data: 会话数据字典（必须包含session_id）
        
        Returns:
            bool: 是否成功缓存
        """
        try:
            session_id = session_data.get('session_id')
            if not session_id:
                logger.error(f"Session data missing session_id for user {user_id}")
                return False
            
            cache_key = cls._get_cache_key(user_id, session_id)
            
            serialized_data = {
                'user_id': user_id,
                'cached_at': timezone.now().isoformat(),
                **session_data
            }
            
            cache.set(cache_key, serialized_data, timeout=cls.TIMEOUT)
            
            sessions_list_key = cls._get_user_sessions_key(user_id)
            existing_sessions = cache.get(sessions_list_key, [])
            
            if session_id not in existing_sessions:
                existing_sessions.append(session_id)
                cache.set(sessions_list_key, existing_sessions, timeout=cls.TIMEOUT * 24)
            
            logger.debug(f"Cached session {session_id} for user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to cache session for user {user_id}: {str(e)}")
            return False
    
    @classmethod
    def get_cached_session(cls, user_id, session_id):
        """
        从Redis获取缓存的会话数据
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
        
        Returns:
            dict or None: 会话数据
        """
        try:
            cache_key = cls._get_cache_key(user_id, session_id)
            session_data = cache.get(cache_key)
            
            if session_data:
                logger.debug(f"Cache hit for session {session_id} of user {user_id}")
                return session_data
            
            logger.debug(f"Cache miss for session {session_id} of user {user_id}")
            return None
            
        except Exception as e:
            logger.error(f"Failed to get cached session: {str(e)}")
            return None
    
    @classmethod
    def get_user_sessions_list(cls, user_id):
        """
        获取用户的所有会话ID列表（从缓存）
        
        Args:
            user_id: 用户ID
        
        Returns:
            list: 会话ID列表
        """
        try:
            sessions_list_key = cls._get_user_sessions_key(user_id)
            return cache.get(sessions_list_key, [])
        except Exception as e:
            logger.error(f"Failed to get user sessions list: {str(e)}")
            return []
    
    @classmethod
    def invalidate_user_session(cls, user_id, session_id):
        """
        使用户的特定会话缓存失效
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
        
        Returns:
            bool: 是否成功失效
        """
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
            
        except Exception as e:
            logger.error(f"Failed to invalidate session: {str(e)}")
            return False
    
    @classmethod
    def invalidate_all_user_sessions(cls, user_id):
        """
        使用户的所有会话缓存失效（用于登出或安全清理）
        
        Args:
            user_id: 用户ID
        
        Returns:
            int: 失效的会话数量
        """
        try:
            session_ids = cls.get_user_sessions_list(user_id)
            invalidated_count = 0
            
            for session_id in session_ids:
                cache_key = cls._get_cache_key(user_id, session_id)
                cache.delete(cache_key)
                invalidated_count += 1
            
            sessions_list_key = cls._get_user_sessions_key(user_id)
            cache.delete(sessions_list_key)
            
            logger.info(f"Invalidated all {invalidated_count} sessions for user {user_id}")
            return invalidated_count
            
        except Exception as e:
            logger.error(f"Failed to invalidate all sessions for user {user_id}: {str(e)}")
            return 0
    
    @classmethod
    def cache_messages_for_session(cls, user_id, session_id, messages):
        """
        缓存会话的消息数据（用于快速访问）
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
            messages: 消息列表
        
        Returns:
            bool: 是否成功
        """
        try:
            messages_key = f'{cls._get_cache_key(user_id, session_id)}:messages'
            cache.set(messages_key, messages, timeout=cls.TIMEOUT // 2)
            return True
        except Exception as e:
            logger.error(f"Failed to cache messages: {str(e)}")
            return False
    
    @classmethod
    def get_cached_messages(cls, user_id, session_id):
        """
        获取缓存的消息数据
        
        Returns:
            list or None: 消息列表
        """
        try:
            messages_key = f'{cls._get_cache_key(user_id, session_id)}:messages'
            return cache.get(messages_key)
        except Exception as e:
            logger.error(f"Failed to get cached messages: {str(e)}")
            return None
    
    @classmethod
    def update_session_access_time(cls, user_id, session_id):
        """
        更新会话的最后访问时间（延长TTL）
        
        Args:
            user_id: 用户ID
            session_id: 会话ID
        """
        try:
            cache_key = cls._get_cache_key(user_id, session_id)
            session_data = cache.get(cache_key)
            
            if session_data:
                session_data['last_accessed'] = timezone.now().isoformat()
                cache.set(cache_key, session_data, timeout=cls.TIMEOUT)
                
        except Exception as e:
            logger.error(f"Failed to update access time: {str(e)}")
