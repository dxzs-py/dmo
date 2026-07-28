"""
Ollama VL Embedding Provider

支持通过 Ollama 调用多模态 VL Embedding 模型（如 Qwen3-VL-Embedding-2B）。

与标准 OllamaEmbeddings 的区别：
1. VL 模型基于 qwen3vl 架构，Ollama 可能不支持 /api/embed 端点
2. 通过 /api/generate 端点调用，从模型输出中提取 embedding 向量
3. 支持 MRL 维度截断（64~2048）
4. 自动模型预热与重试

推荐模型（Ollama 第三方库）：
- MedAIBase/Qwen3-VL-Embedding:2b  (2048 维，多模态，MRL 64~2048)

注意：如果只需要纯文本 embedding，推荐使用官方库的 qwen3-embedding:0.6b
（arch=qwen3，原生支持 /api/embed，兼容性更好）

下载：
    ollama pull MedAIBase/Qwen3-VL-Embedding:2b
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import requests
from langchain_core.embeddings import Embeddings

from Django_xm.apps.ai_engine.config import settings

logger = logging.getLogger(__name__)


def get_provider_config() -> dict[str, Any]:
    return {
        "base_url": getattr(settings, "ollama_base_url", "http://localhost:11435"),
        "model": "MedAIBase/Qwen3-VL-Embedding:2b",
    }


class OllamaVLEmbeddings(Embeddings):
    """Ollama VL Embedding Provider

    支持多模态 VL Embedding 模型，通过 Ollama HTTP API 调用。
    优先尝试 /api/embed 端点，回退到 /api/generate + embedding 提取。
    """

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
        dimensions: int | None = None,
        keep_alive: str = "5m",
        timeout: int = 180,
        max_retries: int = 2,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.dimensions = dimensions
        self.keep_alive = keep_alive
        self.timeout = timeout
        self.max_retries = max_retries
        self._model_loaded = False
        self._embed_method: str | None = None  # 缓存可用的 embed 方法

    def _ensure_model_loaded(self) -> None:
        """确保模型已加载到 Ollama 内存中（预热）"""
        if self._model_loaded:
            return
        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": " ",
                    "keep_alive": self.keep_alive,
                    "options": {"num_predict": 1},
                    "stream": False,
                },
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                self._model_loaded = True
                logger.info(f"VL Embedding 模型预热成功: {self.model}")
            else:
                logger.warning(
                    f"VL Embedding 模型预热失败: {resp.status_code} {resp.text[:200]}"
                )
        except requests.exceptions.Timeout:
            logger.warning(f"VL Embedding 模型预热超时: {self.model}")
        except Exception as e:
            logger.warning(f"VL Embedding 模型预热异常: {e}")

    def _embed_via_api_embed(self, text: str) -> list[float] | None:
        """通过 /api/embed 端点获取 embedding（Ollama 0.5+ 原生支持）"""
        try:
            payload: dict[str, Any] = {
                "model": self.model,
                "input": text,
                "keep_alive": self.keep_alive,
            }
            if self.dimensions:
                payload["options"] = {"num_dim": self.dimensions}

            resp = requests.post(
                f"{self.base_url}/api/embed",
                json=payload,
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                embeddings = data.get("embeddings", [])
                if embeddings and embeddings[0]:
                    return embeddings[0]
            logger.debug(
                f"/api/embed 失败: status={resp.status_code}, body={resp.text[:200]}"
            )
        except Exception as e:
            logger.debug(f"/api/embed 异常: {e}")
        return None

    def _embed_via_api_generate(self, text: str) -> list[float] | None:
        """通过 /api/generate 端点获取 embedding

        对于 qwen3vl 架构的 embedding 模型，Ollama 可能不支持 /api/embed。
        此方法通过 /api/generate 调用模型，从响应中提取 embedding 向量。

        Qwen3-VL-Embedding 模型在 generate 模式下会输出 JSON 格式的 embedding 向量。
        """
        try:
            payload: dict[str, Any] = {
                "model": self.model,
                "prompt": text,
                "keep_alive": self.keep_alive,
                "stream": False,
                "options": {},
            }
            if self.dimensions:
                payload["options"]["num_dim"] = self.dimensions

            resp = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                response_text = data.get("response", "").strip()

                # 尝试从响应中解析 embedding 向量
                # VL Embedding 模型可能输出 JSON 数组格式的向量
                if response_text:
                    embedding = self._parse_embedding_from_response(response_text)
                    if embedding:
                        return embedding

                # 如果响应为空或无法解析，检查 context 字段
                # Ollama 的 context 包含 token IDs，不是 embedding 向量
                logger.debug(
                    f"/api/generate 返回非向量响应: {response_text[:200] if response_text else 'empty'}"
                )
            else:
                logger.debug(
                    f"/api/generate 失败: status={resp.status_code}, body={resp.text[:200]}"
                )
        except Exception as e:
            logger.debug(f"/api/generate 异常: {e}")
        return None

    def _embed_via_api_embeddings(self, text: str) -> list[float] | None:
        """通过 /api/embeddings 端点获取 embedding（旧版 Ollama 兼容）"""
        try:
            payload: dict[str, Any] = {
                "model": self.model,
                "prompt": text,
                "keep_alive": self.keep_alive,
            }
            if self.dimensions:
                payload["options"] = {"num_dim": self.dimensions}

            resp = requests.post(
                f"{self.base_url}/api/embeddings",
                json=payload,
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                embedding = data.get("embedding")
                if embedding:
                    return embedding
            logger.debug(
                f"/api/embeddings 失败: status={resp.status_code}, body={resp.text[:200]}"
            )
        except Exception as e:
            logger.debug(f"/api/embeddings 异常: {e}")
        return None

    @staticmethod
    def _parse_embedding_from_response(text: str) -> list[float] | None:
        """从模型输出文本中解析 embedding 向量

        VL Embedding 模型可能输出以下格式：
        1. JSON 数组: [0.1, 0.2, ...]
        2. 逗号分隔数值: 0.1, 0.2, ...
        3. 嵌入在 JSON 对象中: {"embedding": [0.1, 0.2, ...]}
        """
        text = text.strip()

        # 格式 1: JSON 数组
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], (int, float)):
                    return [float(x) for x in parsed]
            except (json.JSONDecodeError, ValueError):
                pass

        # 格式 3: JSON 对象包含 embedding
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
                for key in ("embedding", "embeddings", "vector", "data"):
                    if key in parsed:
                        val = parsed[key]
                        if isinstance(val, list):
                            if val and isinstance(val[0], (int, float)):
                                return [float(x) for x in val]
                            if val and isinstance(val[0], list):
                                return [float(x) for x in val[0]]
            except (json.JSONDecodeError, ValueError):
                pass

        # 格式 2: 逗号分隔数值
        if "," in text and all(
            c in "0123456789.,-+eE \t\n" for c in text
        ):
            try:
                values = [float(x.strip()) for x in text.split(",") if x.strip()]
                if len(values) > 10:  # embedding 向量至少几十维
                    return values
            except ValueError:
                pass

        return None

    def _embed_single(self, text: str) -> list[float]:
        """获取单条文本的 embedding 向量（带重试和方法探测）"""
        # 如果已知可用方法，直接使用
        if self._embed_method == "api_embed":
            result = self._embed_via_api_embed(text)
            if result:
                return result
            self._embed_method = None  # 方法失效，重新探测

        if self._embed_method == "api_generate":
            result = self._embed_via_api_generate(text)
            if result:
                return result
            self._embed_method = None

        if self._embed_method == "api_embeddings":
            result = self._embed_via_api_embeddings(text)
            if result:
                return result
            self._embed_method = None

        # 首次调用：按优先级探测可用方法
        # 1. /api/embed（Ollama 0.5+ 原生，最快最稳定）
        result = self._embed_via_api_embed(text)
        if result:
            self._embed_method = "api_embed"
            logger.info(f"VL Embedding 使用 /api/embed 方法: {self.model}")
            return result

        # 2. /api/generate + 从响应提取（VL 模型专用路径）
        result = self._embed_via_api_generate(text)
        if result:
            self._embed_method = "api_generate"
            logger.info(f"VL Embedding 使用 /api/generate 方法: {self.model}")
            return result

        # 3. /api/embeddings（旧版 Ollama 兼容）
        result = self._embed_via_api_embeddings(text)
        if result:
            self._embed_method = "api_embeddings"
            logger.info(f"VL Embedding 使用 /api/embeddings 方法: {self.model}")
            return result

        # 所有方法都失败，尝试预热后重试一次
        if not self._model_loaded:
            logger.info(f"VL Embedding 所有方法失败，尝试预热模型后重试: {self.model}")
            self._ensure_model_loaded()
            if self._model_loaded:
                result = self._embed_via_api_embed(text)
                if result:
                    self._embed_method = "api_embed"
                    return result
                result = self._embed_via_api_generate(text)
                if result:
                    self._embed_method = "api_generate"
                    return result
                result = self._embed_via_api_embeddings(text)
                if result:
                    self._embed_method = "api_embeddings"
                    return result

        raise RuntimeError(
            f"无法从 Ollama 获取 VL Embedding: model={self.model}。"
            f"请确认：\n"
            f"  1. 模型已下载: ollama pull {self.model}\n"
            f"  2. Ollama 服务正常运行: ollama serve\n"
            f"  3. GPU 显存充足（至少 4GB 空闲）\n"
            f"  4. Ollama 版本支持 qwen3vl 架构（>= 0.6）\n"
            f"  如果问题持续，建议使用纯文本版本: ollama pull qwen3-embedding:0.6b"
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量获取文本 embedding"""
        return [self._embed_single(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        """获取查询文本 embedding"""
        return self._embed_single(text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """异步批量获取文本 embedding"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.embed_documents, texts)

    async def aembed_query(self, text: str) -> list[float]:
        """异步获取查询文本 embedding"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.embed_query, text)


def create_embedding(
    model: str | None = None,
    dimensions: int | None = None,
    **kwargs: Any,
) -> Embeddings:
    """创建 Ollama VL Embedding 实例

    Args:
        model: Ollama 中已 pull 的 VL Embedding 模型名
        dimensions: MRL 截断维度（可选）。Qwen3-VL-Embedding 支持 64~2048。
        **kwargs: 透传参数（keep_alive, timeout 等）
    """
    cfg = get_provider_config()
    return OllamaVLEmbeddings(
        model=model or cfg["model"],
        base_url=cfg["base_url"],
        dimensions=dimensions,
        keep_alive=kwargs.get("keep_alive", "5m"),
        timeout=kwargs.get("timeout", 180),
    )
