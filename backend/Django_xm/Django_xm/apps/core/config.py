"""
统一配置管理模块
使用 Pydantic Settings 管理所有配置项，支持从环境变量和 .env 文件加载
向后兼容：使用Django settings作为主要配置源
"""

import os
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from django.conf import settings as django_settings


class Settings(BaseSettings):
    """
    应用配置类
    
    所有配置项都可以通过环境变量或 .env 文件设置
    优先级：环境变量 > Django settings > Pydantic默认值
    """
    
    # ==================== OpenAI 配置 ====================
    openai_api_key: str = Field(
        default="",
        description="OpenAI API 密钥，必须设置"
    )
    
    openai_api_base: str = Field(
        default="https://api.openai.com/v1",
        description="OpenAI API 基础 URL"
    )
    
    openai_model: str = Field(
        default="gpt-4o",
        description="默认使用的 OpenAI 模型"
    )
    
    openai_temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="模型温度参数，控制输出的随机性"
    )
    
    openai_max_tokens: Optional[int] = Field(
        default=None,
        description="最大生成 token 数，None 表示使用模型默认值"
    )
    
    openai_streaming: bool = Field(
        default=True,
        description="是否默认启用流式输出"
    )
    
    # ==================== Tavily 搜索配置 ====================
    tavily_api_key: str = Field(
        default="",
        description="Tavily 搜索 API 密钥（可选）"
    )
    
    tavily_max_results: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Tavily 搜索返回的最大结果数"
    )
    
    # ==================== 高德地图配置 ====================
    amap_key: str = Field(
        default="",
        description="高德地图 API 密钥（可选，用于天气查询等服务）"
    )
    
    # ==================== 服务器配置 ====================
    server_host: str = Field(
        default="0.0.0.0",
        description="服务器监听地址"
    )
    
    server_port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        description="服务器监听端口"
    )
    
    server_reload: bool = Field(
        default=True,
        description="开发模式下是否自动重载"
    )
    
    # ==================== 日志配置 ====================
    log_level: str = Field(
        default="INFO",
        description="日志级别：DEBUG, INFO, WARNING, ERROR, CRITICAL"
    )
    
    log_file: str = Field(
        default="logs/app.log",
        description="日志文件路径"
    )
    
    log_rotation: str = Field(
        default="100 MB",
        description="日志文件轮转大小"
    )
    
    log_retention: str = Field(
        default="30 days",
        description="日志文件保留时间"
    )
    
    # ==================== 应用配置 ====================
    app_name: str = Field(
        default="LC-StudyLab",
        description="应用名称"
    )
    
    app_version: str = Field(
        default="0.1.0",
        description="应用版本"
    )
    
    debug: bool = Field(
        default=False,
        description="是否启用调试模式"
    )
    
    # ==================== 数据目录配置 ====================
    DATA_DIR: str = Field(
        default="data",
        description="数据存储根目录"
    )
    
    # ==================== Agent 配置 ====================
    agent_max_iterations: int = Field(
        default=15,
        ge=1,
        le=100,
        description="Agent 最大迭代次数"
    )
    
    agent_max_execution_time: Optional[float] = Field(
        default=None,
        description="Agent 最大执行时间（秒），None 表示无限制"
    )
    
    # ==================== RAG 配置 ====================
    # Embedding 配置
    embedding_model: str = Field(
        default="text-embedding-3-small",
        description="OpenAI Embedding 模型名称"
    )
    
    embedding_batch_size: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Embedding 批处理大小"
    )
    
    # 文本分块配置
    chunk_size: int = Field(
        default=1000,
        ge=100,
        le=10000,
        description="文本分块大小（字符数）"
    )
    
    chunk_overlap: int = Field(
        default=200,
        ge=0,
        le=1000,
        description="文本分块重叠大小（字符数）"
    )
    
    # 向量库配置
    vector_store_type: str = Field(
        default="faiss",
        description="向量库类型：faiss, inmemory, chroma"
    )
    
    vector_store_path: str = Field(
        default="data/indexes",
        description="向量库存储路径"
    )
    
    # 检索配置
    retriever_search_type: str = Field(
        default="similarity",
        description="检索类型：similarity, mmr, similarity_score_threshold"
    )
    
    retriever_k: int = Field(
        default=4,
        ge=1,
        le=20,
        description="检索返回的文档数量"
    )
    
    retriever_score_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="相似度阈值（仅用于 similarity_score_threshold 模式）"
    )
    
    retriever_fetch_k: int = Field(
        default=20,
        ge=1,
        le=100,
        description="MMR 检索的候选文档数量"
    )
    
    # RAG Agent 配置
    rag_agent_max_iterations: int = Field(
        default=10,
        ge=1,
        le=50,
        description="RAG Agent 最大迭代次数"
    )
    
    rag_agent_return_source_documents: bool = Field(
        default=True,
        description="是否返回来源文档"
    )
    
    # 数据路径配置
    data_documents_path: str = Field(
        default="data/documents",
        description="文档存储路径"
    )
    
    data_uploads_path: str = Field(
        default="data/uploads",
        description="上传文件存储路径"
    )
    
    # Pydantic Settings 配置
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    
    def __init__(self, *args, **kwargs):
        """初始化时从Django settings加载配置"""
        super().__init__(*args, **kwargs)
        
        # 从Django settings加载配置（如果可用）
        try:
            # OpenAI配置
            if hasattr(django_settings, 'OPENAI_API_KEY') and django_settings.OPENAI_API_KEY:
                self.openai_api_key = django_settings.OPENAI_API_KEY
            if hasattr(django_settings, 'OPENAI_API_BASE') and django_settings.OPENAI_API_BASE:
                self.openai_api_base = django_settings.OPENAI_API_BASE
            if hasattr(django_settings, 'OPENAI_MODEL') and django_settings.OPENAI_MODEL:
                self.openai_model = django_settings.OPENAI_MODEL
            if hasattr(django_settings, 'OPENAI_TEMPERATURE') and django_settings.OPENAI_TEMPERATURE:
                self.openai_temperature = float(django_settings.OPENAI_TEMPERATURE)
            if hasattr(django_settings, 'OPENAI_STREAMING') and django_settings.OPENAI_STREAMING:
                self.openai_streaming = django_settings.OPENAI_STREAMING
            if hasattr(django_settings, 'OPENAI_MAX_TOKENS') and django_settings.OPENAI_MAX_TOKENS:
                self.openai_max_tokens = int(django_settings.OPENAI_MAX_TOKENS)
            
            # Tavily配置
            if hasattr(django_settings, 'TAVILY_API_KEY') and django_settings.TAVILY_API_KEY:
                self.tavily_api_key = django_settings.TAVILY_API_KEY
            if hasattr(django_settings, 'TAVILY_MAX_RESULTS') and django_settings.TAVILY_MAX_RESULTS:
                self.tavily_max_results = int(django_settings.TAVILY_MAX_RESULTS)
            
            # AMap配置
            if hasattr(django_settings, 'AMAP_KEY') and django_settings.AMAP_KEY:
                self.amap_key = django_settings.AMAP_KEY
            
            # 日志配置
            if hasattr(django_settings, 'LOG_LEVEL') and django_settings.LOG_LEVEL:
                self.log_level = django_settings.LOG_LEVEL
            if hasattr(django_settings, 'LOG_FILE') and django_settings.LOG_FILE:
                self.log_file = django_settings.LOG_FILE
            if hasattr(django_settings, 'LOG_ROTATION') and django_settings.LOG_ROTATION:
                self.log_rotation = django_settings.LOG_ROTATION
            if hasattr(django_settings, 'LOG_RETENTION') and django_settings.LOG_RETENTION:
                self.log_retention = django_settings.LOG_RETENTION
            
            # 应用配置
            if hasattr(django_settings, 'APP_NAME') and django_settings.APP_NAME:
                self.app_name = django_settings.APP_NAME
            if hasattr(django_settings, 'APP_VERSION') and django_settings.APP_VERSION:
                self.app_version = django_settings.APP_VERSION
            if hasattr(django_settings, 'DEBUG') and django_settings.DEBUG:
                self.debug = django_settings.DEBUG
            
            # 向量存储配置
            if hasattr(django_settings, 'VECTOR_STORE_PATH') and django_settings.VECTOR_STORE_PATH:
                self.vector_store_path = str(django_settings.VECTOR_STORE_PATH)
            if hasattr(django_settings, 'VECTOR_STORE_TYPE') and django_settings.VECTOR_STORE_TYPE:
                self.vector_store_type = django_settings.VECTOR_STORE_TYPE
            if hasattr(django_settings, 'EMBEDDING_MODEL') and django_settings.EMBEDDING_MODEL:
                self.embedding_model = django_settings.EMBEDDING_MODEL
            if hasattr(django_settings, 'EMBEDDING_BATCH_SIZE') and django_settings.EMBEDDING_BATCH_SIZE:
                self.embedding_batch_size = int(django_settings.EMBEDDING_BATCH_SIZE)
            
            # 文本分块配置
            if hasattr(django_settings, 'CHUNK_SIZE') and django_settings.CHUNK_SIZE:
                self.chunk_size = int(django_settings.CHUNK_SIZE)
            if hasattr(django_settings, 'CHUNK_OVERLAP') and django_settings.CHUNK_OVERLAP:
                self.chunk_overlap = int(django_settings.CHUNK_OVERLAP)
            
            # 检索器配置
            if hasattr(django_settings, 'RETRIEVER_SEARCH_TYPE') and django_settings.RETRIEVER_SEARCH_TYPE:
                self.retriever_search_type = django_settings.RETRIEVER_SEARCH_TYPE
            if hasattr(django_settings, 'RETRIEVER_K') and django_settings.RETRIEVER_K:
                self.retriever_k = int(django_settings.RETRIEVER_K)
            if hasattr(django_settings, 'RETRIEVER_SCORE_THRESHOLD') and django_settings.RETRIEVER_SCORE_THRESHOLD:
                self.retriever_score_threshold = float(django_settings.RETRIEVER_SCORE_THRESHOLD)
            if hasattr(django_settings, 'RETRIEVER_FETCH_K') and django_settings.RETRIEVER_FETCH_K:
                self.retriever_fetch_k = int(django_settings.RETRIEVER_FETCH_K)
            
            # Agent配置
            if hasattr(django_settings, 'AGENT_MAX_ITERATIONS') and django_settings.AGENT_MAX_ITERATIONS:
                self.agent_max_iterations = int(django_settings.AGENT_MAX_ITERATIONS)
            if hasattr(django_settings, 'AGENT_MAX_EXECUTION_TIME') and django_settings.AGENT_MAX_EXECUTION_TIME:
                self.agent_max_execution_time = float(django_settings.AGENT_MAX_EXECUTION_TIME)
            
            # RAG Agent配置
            if hasattr(django_settings, 'RAG_AGENT_MAX_ITERATIONS') and django_settings.RAG_AGENT_MAX_ITERATIONS:
                self.rag_agent_max_iterations = int(django_settings.RAG_AGENT_MAX_ITERATIONS)
            if hasattr(django_settings, 'RAG_AGENT_RETURN_SOURCE_DOCUMENTS') and django_settings.RAG_AGENT_RETURN_SOURCE_DOCUMENTS:
                self.rag_agent_return_source_documents = django_settings.RAG_AGENT_RETURN_SOURCE_DOCUMENTS
            
            # 数据目录配置
            if hasattr(django_settings, 'DATA_DIR') and django_settings.DATA_DIR:
                self.DATA_DIR = str(django_settings.DATA_DIR)
            if hasattr(django_settings, 'DOCUMENTS_DIR') and django_settings.DOCUMENTS_DIR:
                self.data_documents_path = str(django_settings.DOCUMENTS_DIR)
            if hasattr(django_settings, 'INDEXES_DIR') and django_settings.INDEXES_DIR:
                self.vector_store_path = str(django_settings.INDEXES_DIR)
            if hasattr(django_settings, 'UPLOADS_DIR') and django_settings.UPLOADS_DIR:
                self.data_uploads_path = str(django_settings.UPLOADS_DIR)
                
        except Exception:
            # 如果Django未加载，继续使用Pydantic默认值
            pass
    
    def validate_required_keys(self) -> None:
        """
        验证必需的配置项是否已设置
        
        Raises:
            ValueError: 如果必需的配置项未设置
        """
        if not self.openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY 未设置！请在环境变量或 .env 文件中设置。"
            )
    
    def get_openai_config(self) -> dict:
        """
        获取 OpenAI 配置字典
        
        Returns:
            包含 OpenAI 配置的字典
        """
        config = {
            "api_key": self.openai_api_key,
            "base_url": self.openai_api_base,
            "model": self.openai_model,
            "temperature": self.openai_temperature,
        }
        
        if self.openai_max_tokens is not None:
            config["max_tokens"] = self.openai_max_tokens
            
        return config
    
    def get_tavily_config(self) -> dict:
        """
        获取 Tavily 配置字典
        
        Returns:
            包含 Tavily 配置的字典
        """
        return {
            "api_key": self.tavily_api_key,
            "max_results": self.tavily_max_results,
        }


_settings_instance = None


def get_settings():
    """延迟获取 settings 实例，确保 Django settings 已加载"""
    global _settings_instance
    if _settings_instance is None:
        try:
            _settings_instance = Settings()
        except Exception as e:
            print(f"⚠️  配置初始化失败: {e}")
            _settings_instance = Settings()
    return _settings_instance


def validate_settings() -> None:
    """验证配置的辅助函数"""
    try:
        s = get_settings()
        s.validate_required_keys()
    except ValueError as e:
        # 在开发环境下，如果没有设置 API Key，只打印警告而不抛出异常
        try:
            s = get_settings()
            if s.debug:
                print(f"⚠️  配置警告: {e}")
            else:
                raise
        except:
            raise


import sys
if "pytest" not in sys.modules:
    # 延迟验证，允许在导入后再设置环境变量
    pass


# 兼容性：提供 settings 实例，但延迟初始化
class _SettingsProxy:
    """代理类，延迟初始化 settings"""
    def __getattr__(self, name):
        # 使用 object.__getattribute__ 避免递归
        try:
            s = object.__getattribute__(self, '_settings_instance')
        except AttributeError:
            s = get_settings()
            object.__setattr__(self, '_settings_instance', s)
        return getattr(s, name)
    
    def __setattr__(self, name, value):
        try:
            s = object.__getattribute__(self, '_settings_instance')
        except AttributeError:
            s = get_settings()
            object.__setattr__(self, '_settings_instance', s)
        return setattr(s, name, value)


settings = _SettingsProxy()


import logging
from pathlib import Path


def get_logger(name: str) -> logging.Logger:
    """
    获取指定名称的 logger
    
    Args:
        name: logger 名称，通常使用模块的 __name__
        
    Returns:
        配置好的 logger 实例
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, settings.log_level.upper()))
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, settings.log_level.upper()))
    formatter = logging.Formatter('%(levelname)s %(asctime)s - %(name)s - %(message)s')
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    logger.propagate = False
    return logger


# 可选：集成 loguru 的辅助函数（如果已安装）
def setup_loguru_logging() -> None:
    """
    配置 loguru 日志系统（可选）
    
    使用 loguru 提供结构化日志，支持：
    - 控制台彩色输出
    - 文件日志轮转
    - 自动清理过期日志
    - 异常追踪
    """
    try:
        import sys
        from loguru import logger
        
        # 移除默认的 handler
        logger.remove()
        
        # ==================== 控制台日志 ====================
        # 添加彩色控制台输出，格式化更易读
        logger.add(
            sys.stderr,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
                "<level>{message}</level>"
            ),
            level=settings.log_level,
            colorize=True,
            backtrace=True,
            diagnose=True,
        )
        
        # ==================== 文件日志 ====================
        # 确保日志目录存在
        log_path = Path(settings.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 添加文件日志，支持轮转和自动清理
        logger.add(
            settings.log_file,
            format=(
                "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
                "{level: <8} | "
                "{name}:{function}:{line} | "
                "{message}"
            ),
            level=settings.log_level,
            rotation=settings.log_rotation,
            retention=settings.log_retention,
            compression="zip",
            backtrace=True,
            diagnose=True,
            enqueue=True,
        )
        
        logger.info(f"📝 loguru 日志系统初始化完成 - 级别: {settings.log_level}, 文件: {settings.log_file}")
        
    except ImportError:
        # loguru 未安装，使用标准 logging
        pass
