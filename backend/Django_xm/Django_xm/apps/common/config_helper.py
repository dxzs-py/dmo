"""
配置助手模块
提供统一的配置访问接口
"""
import logging
from typing import Any, Optional
from django.conf import settings as django_settings

logger = logging.getLogger(__name__)


def get_config(key: str, default: Any = None) -> Any:
    """
    从 Django settings 中获取配置值
    
    Args:
        key: 配置键名
        default: 默认值
    
    Returns:
        配置值
    """
    try:
        return getattr(django_settings, key, default)
    except Exception as e:
        logger.warning(f"获取配置失败 {key}: {e}")
        return default


def get_openai_config() -> dict:
    """
    获取 OpenAI 相关配置
    
    Returns:
        OpenAI 配置字典
    """
    return {
        'api_key': get_config('OPENAI_API_KEY'),
        'api_base': get_config('OPENAI_API_BASE'),
        'model': get_config('OPENAI_MODEL'),
        'temperature': get_config('OPENAI_TEMPERATURE', 0.7),
        'max_tokens': get_config('OPENAI_MAX_TOKENS'),
        'streaming': get_config('OPENAI_STREAMING', True),
    }


def get_database_config() -> dict:
    """
    获取数据库配置
    
    Returns:
        数据库配置字典
    """
    databases = get_config('DATABASES', {})
    default_db = databases.get('default', {})
    return {
        'engine': default_db.get('ENGINE'),
        'name': default_db.get('NAME'),
        'user': default_db.get('USER'),
        'host': default_db.get('HOST'),
        'port': default_db.get('PORT'),
    }


def is_debug_mode() -> bool:
    """
    检查是否为调试模式
    
    Returns:
        是否为调试模式
    """
    return get_config('DEBUG', False)


def get_allowed_hosts() -> list:
    """
    获取允许的主机列表
    
    Returns:
        允许的主机列表
    """
    return get_config('ALLOWED_HOSTS', [])


def get_cors_allowed_origins() -> list:
    """
    获取 CORS 允许的来源
    
    Returns:
        CORS 允许的来源列表
    """
    return get_config('CORS_ALLOWED_ORIGINS', [])
