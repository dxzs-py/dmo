"""
统一配置管理模块
使用 Pydantic Settings 管理所有配置项，支持从环境变量和 .env 文件加载
"""

from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    应用配置类

    所有配置项都可以通过环境变量或 .env 文件设置
    优先级：环境变量 > .env 文件 > 默认值
    """

    openai_api_key: str = Field(
        default="",
        description="OpenAI API 密钥"
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
        description="模型温度参数"
    )

    openai_max_tokens: Optional[int] = Field(
        default=None,
        description="最大生成 token 数"
    )

    openai_streaming: bool = Field(
        default=True,
        description="是否默认启用流式输出"
    )

    tavily_api_key: str = Field(
        default="",
        description="Tavily 搜索 API 密钥"
    )

    tavily_max_results: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Tavily 搜索返回的最大结果数"
    )

    amap_key: str = Field(
        default="",
        description="高德地图 API 密钥"
    )

    embedding_model: str = Field(
        default="text-embedding-3-small",
        description="OpenAI Embedding 模型名称"
    )

    vector_store_path: str = Field(
        default="data/indexes",
        description="向量库存储路径"
    )

    vector_store_type: str = Field(
        default="faiss",
        description="向量库类型"
    )

    retriever_search_type: str = Field(
        default="similarity",
        description="检索类型"
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
        description="相似度阈值"
    )

    retriever_fetch_k: int = Field(
        default=20,
        ge=1,
        le=100,
        description="MMR 检索的候选文档数量"
    )

    chunk_size: int = Field(
        default=1000,
        ge=100,
        le=10000,
        description="文本分块大小"
    )

    chunk_overlap: int = Field(
        default=200,
        ge=0,
        le=1000,
        description="文本分块重叠大小"
    )

    agent_max_iterations: int = Field(
        default=15,
        ge=1,
        le=100,
        description="Agent 最大迭代次数"
    )

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

    data_documents_path: str = Field(
        default="data/documents",
        description="文档存储路径"
    )

    data_uploads_path: str = Field(
        default="data/uploads",
        description="上传文件存储路径"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def validate_required_keys(self) -> None:
        """验证必需的配置项"""
        if not self.openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY 未设置！请在环境变量或 .env 文件中设置。"
            )

    def get_openai_config(self) -> dict:
        """获取 OpenAI 配置字典"""
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
        """获取 Tavily 配置字典"""
        return {
            "api_key": self.tavily_api_key,
            "max_results": self.tavily_max_results,
        }


settings = Settings()


import logging


def get_logger(name: str) -> logging.Logger:
    """获取配置好的日志记录器"""
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    formatter = logging.Formatter(
        '%(levelname)s %(asctime)s - %(name)s - %(message)s'
    )
    console_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.propagate = False

    return logger
