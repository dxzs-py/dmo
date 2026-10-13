"""E5-06：qianfan/ollama embedding provider 显式 60s 超时测试。

缺陷（修复前）：openai provider 有 timeout=60（openai_embedding.py），qianfan/ollama
均未配置任何超时——provider 挂起时 embed_query/embed_documents 阻塞时长不可控
（检索入口同步等待，索引侧仅靠 Celery soft_time_limit 兜底）。

修复契约（本文件锁定）：
- qianfan：init_kwargs 注入 {"LLM_API_RETRY_TIMEOUT": 60}（qianfan SDK Config 键，
  http_client 以 retry_config.timeout 消费，见 qianfan/resources/http_client.py）；
- ollama：client_kwargs 注入 {"timeout": 60.0}（httpx Client/AsyncClient 超时）；
- 两 provider 均允许调用方经 create_embedding kwargs 显式覆盖（与 openai 对齐）。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.ai_engine.tests.test_embedding_provider_timeouts --noinput
"""

import os
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import SimpleTestCase

_QIANFAN_PROVIDER = "Django_xm.apps.ai_engine.providers.qianfan_embedding"
_OLLAMA_PROVIDER = "Django_xm.apps.ai_engine.providers.ollama_embedding"


class QianfanEmbeddingTimeoutTests(SimpleTestCase):
    """qianfan provider：init_kwargs 携带 LLM_API_RETRY_TIMEOUT=60。"""

    def test_default_timeout_injected(self):
        with (
            mock.patch(f"{_QIANFAN_PROVIDER}.get_provider_config") as cfg,
            mock.patch("langchain_community.embeddings.QianfanEmbeddingsEndpoint") as endpoint,
        ):
            cfg.return_value = {"qianfan_ak": "ak-test", "endpoint": "https://aip.baidubce.com"}
            from Django_xm.apps.ai_engine.providers.qianfan_embedding import create_embedding

            create_embedding()

        init_kwargs = endpoint.call_args.kwargs
        self.assertEqual(init_kwargs["init_kwargs"], {"LLM_API_RETRY_TIMEOUT": 60})

    def test_caller_can_override(self):
        """调用方显式传 init_kwargs 时全量覆盖（与 openai provider kwargs 透传口径一致）。"""
        with (
            mock.patch(f"{_QIANFAN_PROVIDER}.get_provider_config") as cfg,
            mock.patch("langchain_community.embeddings.QianfanEmbeddingsEndpoint") as endpoint,
        ):
            cfg.return_value = {"qianfan_ak": "ak-test", "endpoint": ""}
            from Django_xm.apps.ai_engine.providers.qianfan_embedding import create_embedding

            create_embedding(init_kwargs={"LLM_API_RETRY_TIMEOUT": 30})

        self.assertEqual(endpoint.call_args.kwargs["init_kwargs"], {"LLM_API_RETRY_TIMEOUT": 30})


class OllamaEmbeddingTimeoutTests(SimpleTestCase):
    """ollama provider：client_kwargs 携带 httpx timeout=60.0。"""

    def test_default_timeout_injected(self):
        with (
            mock.patch(f"{_OLLAMA_PROVIDER}.get_provider_config") as cfg,
            mock.patch("langchain_ollama.OllamaEmbeddings") as embeddings_cls,
        ):
            cfg.return_value = {"base_url": "http://localhost:11435", "model": "bge-m3"}
            from Django_xm.apps.ai_engine.providers.ollama_embedding import create_embedding

            create_embedding()

        init_kwargs = embeddings_cls.call_args.kwargs
        self.assertEqual(init_kwargs["client_kwargs"], {"timeout": 60.0})

    def test_caller_can_override(self):
        with (
            mock.patch(f"{_OLLAMA_PROVIDER}.get_provider_config") as cfg,
            mock.patch("langchain_ollama.OllamaEmbeddings") as embeddings_cls,
        ):
            cfg.return_value = {"base_url": "http://localhost:11435", "model": "bge-m3"}
            from Django_xm.apps.ai_engine.providers.ollama_embedding import create_embedding

            create_embedding(client_kwargs={"timeout": 120.0})

        self.assertEqual(embeddings_cls.call_args.kwargs["client_kwargs"], {"timeout": 120.0})
