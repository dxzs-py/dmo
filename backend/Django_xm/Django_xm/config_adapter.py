"""
Django配置适配器
将Pydantic配置整合到Django settings中
"""
import os
from typing import Optional

from django.conf import settings as django_settings


class ConfigAdapter:
    """配置适配器类，统一访问配置"""
    
    @property
    def openai_api_key(self) -> str:
        return os.environ.get("OPENAI_API_KEY", getattr(django_settings, "OPENAI_API_KEY", ""))
    
    @property
    def openai_api_base(self) -> str:
        return os.environ.get("OPENAI_API_BASE", getattr(django_settings, "OPENAI_API_BASE", "https://api.openai.com/v1"))
    
    @property
    def openai_model(self) -> str:
        return os.environ.get("OPENAI_MODEL", getattr(django_settings, "OPENAI_MODEL", "gpt-4o"))
    
    @property
    def openai_temperature(self) -> float:
        return float(os.environ.get("OPENAI_TEMPERATURE", getattr(django_settings, "OPENAI_TEMPERATURE", 0.7)))
    
    @property
    def openai_max_tokens(self) -> Optional[int]:
        value = os.environ.get("OPENAI_MAX_TOKENS", getattr(django_settings, "OPENAI_MAX_TOKENS", None))
        return int(value) if value else None
    
    @property
    def openai_streaming(self) -> bool:
        return os.environ.get("OPENAI_STREAMING", getattr(django_settings, "OPENAI_STREAMING", "true")).lower() == "true"
    
    @property
    def tavily_api_key(self) -> str:
        return os.environ.get("TAVILY_API_KEY", getattr(django_settings, "TAVILY_API_KEY", ""))
    
    @property
    def tavily_max_results(self) -> int:
        return int(os.environ.get("TAVILY_MAX_RESULTS", getattr(django_settings, "TAVILY_MAX_RESULTS", 5)))
    
    @property
    def amap_key(self) -> str:
        return os.environ.get("AMAP_KEY", getattr(django_settings, "AMAP_KEY", ""))
    
    @property
    def log_level(self) -> str:
        return os.environ.get("LOG_LEVEL", getattr(django_settings, "LOG_LEVEL", "INFO"))
    
    @property
    def log_file(self) -> str:
        return os.environ.get("LOG_FILE", getattr(django_settings, "LOG_FILE", "logs/app.log"))
    
    @property
    def app_name(self) -> str:
        return os.environ.get("APP_NAME", getattr(django_settings, "APP_NAME", "LC-StudyLab"))
    
    @property
    def app_version(self) -> str:
        return os.environ.get("APP_VERSION", getattr(django_settings, "APP_VERSION", "0.1.0"))
    
    @property
    def debug(self) -> bool:
        return getattr(django_settings, "DEBUG", False)
    
    @property
    def data_dir(self) -> str:
        return os.environ.get("DATA_DIR", getattr(django_settings, "DATA_DIR", "data"))
    
    @property
    def agent_max_iterations(self) -> int:
        return int(os.environ.get("AGENT_MAX_ITERATIONS", getattr(django_settings, "AGENT_MAX_ITERATIONS", 15)))
    
    @property
    def agent_max_execution_time(self) -> Optional[float]:
        value = os.environ.get("AGENT_MAX_EXECUTION_TIME", getattr(django_settings, "AGENT_MAX_EXECUTION_TIME", None))
        return float(value) if value else None
    
    @property
    def embedding_model(self) -> str:
        return os.environ.get("EMBEDDING_MODEL", getattr(django_settings, "EMBEDDING_MODEL", "text-embedding-3-small"))
    
    @property
    def embedding_batch_size(self) -> int:
        return int(os.environ.get("EMBEDDING_BATCH_SIZE", getattr(django_settings, "EMBEDDING_BATCH_SIZE", 100)))
    
    @property
    def chunk_size(self) -> int:
        return int(os.environ.get("CHUNK_SIZE", getattr(django_settings, "CHUNK_SIZE", 1000)))
    
    @property
    def chunk_overlap(self) -> int:
        return int(os.environ.get("CHUNK_OVERLAP", getattr(django_settings, "CHUNK_OVERLAP", 200)))
    
    @property
    def vector_store_type(self) -> str:
        return os.environ.get("VECTOR_STORE_TYPE", getattr(django_settings, "VECTOR_STORE_TYPE", "faiss"))
    
    @property
    def vector_store_path(self) -> str:
        return os.environ.get("VECTOR_STORE_PATH", getattr(django_settings, "VECTOR_STORE_PATH", "data/indexes"))
    
    @property
    def retriever_search_type(self) -> str:
        return os.environ.get("RETRIEVER_SEARCH_TYPE", getattr(django_settings, "RETRIEVER_SEARCH_TYPE", "similarity"))
    
    @property
    def retriever_k(self) -> int:
        return int(os.environ.get("RETRIEVER_K", getattr(django_settings, "RETRIEVER_K", 4)))
    
    @property
    def retriever_score_threshold(self) -> float:
        return float(os.environ.get("RETRIEVER_SCORE_THRESHOLD", getattr(django_settings, "RETRIEVER_SCORE_THRESHOLD", 0.5)))
    
    @property
    def retriever_fetch_k(self) -> int:
        return int(os.environ.get("RETRIEVER_FETCH_K", getattr(django_settings, "RETRIEVER_FETCH_K", 20)))
    
    @property
    def rag_agent_max_iterations(self) -> int:
        return int(os.environ.get("RAG_AGENT_MAX_ITERATIONS", getattr(django_settings, "RAG_AGENT_MAX_ITERATIONS", 10)))
    
    @property
    def rag_agent_return_source_documents(self) -> bool:
        return os.environ.get("RAG_AGENT_RETURN_SOURCE_DOCUMENTS", getattr(django_settings, "RAG_AGENT_RETURN_SOURCE_DOCUMENTS", "true")).lower() == "true"
    
    @property
    def data_documents_path(self) -> str:
        return os.environ.get("DATA_DOCUMENTS_PATH", getattr(django_settings, "DATA_DOCUMENTS_PATH", "data/documents"))
    
    @property
    def data_uploads_path(self) -> str:
        return os.environ.get("DATA_UPLOADS_PATH", getattr(django_settings, "DATA_UPLOADS_PATH", "data/uploads"))


settings = ConfigAdapter()
