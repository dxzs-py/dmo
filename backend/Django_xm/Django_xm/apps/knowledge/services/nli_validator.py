"""
NLI 忠实度验证模型封装

支持中英文双语的声明级忠实度验证：
- 英文：Vectara HHEM-2.1-Open（T5-based cross-encoder，仅英文）
- 中文：达摩院 StructBERT NLI 中文版（BERT-based，3分类：矛盾/蕴涵/中立）
- 通用降级：Embedding 余弦相似度（无需额外模型，复用已有 embedding 服务）

使用方式：
    validator = get_nli_validator()
    if validator.is_available():
        score = validator.predict("原始文档文本", "待验证的声明")
        # score > 0.5 表示声明被原文蕴含

语言检测自动选择对应模型，无需手动指定。
"""

import re
import threading

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)


def _detect_language(text: str) -> str:
    """简单语言检测：根据中文字符占比判断

    Returns:
        "zh" 或 "en"
    """
    if not text:
        return "en"
    cn_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    total = len(text.strip())
    if total == 0:
        return "en"
    return "zh" if cn_chars / total > 0.15 else "en"


class HHEMValidator:
    """Vectara HHEM-2.1-Open 幻觉检测模型封装（仅英文）

    懒加载设计：首次调用 predict() 时才加载模型。
    线程安全：模型加载使用双重检查锁。

    注意：HHEM-2.1-Open 只支持英文，中文输入会返回 -1.0 触发降级。
    """

    _MODEL_NAME = "vectara/hallucination_evaluation_model"

    def __init__(self):
        self._model = None
        self._tokenizer = None
        self._loaded = False
        self._load_failed = False
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        if self._loaded:
            return True
        if self._load_failed:
            return False
        try:
            import torch
            import transformers

            return True
        except ImportError:
            return False

    def predict(self, premise: str, hypothesis: str) -> float:
        self._ensure_loaded()
        if self._model is None or self._tokenizer is None:
            return -1.0

        try:
            return self._do_predict(premise, hypothesis)
        except Exception as e:
            logger.warning(f"HHEM 推理失败: {e}")
            return -1.0

    def _ensure_loaded(self):
        if self._loaded or self._load_failed:
            return

        with self._lock:
            if self._loaded or self._load_failed:
                return

            try:
                import torch
                from transformers import AutoModelForSequenceClassification, AutoTokenizer

                logger.info(f"正在加载 HHEM 模型: {self._MODEL_NAME}")
                self._tokenizer = AutoTokenizer.from_pretrained(self._MODEL_NAME)
                self._model = AutoModelForSequenceClassification.from_pretrained(self._MODEL_NAME)
                self._model.eval()

                device = "cuda" if torch.cuda.is_available() else "cpu"
                self._model = self._model.to(device)
                logger.info(f"HHEM 模型已加载到 {device}")

                self._loaded = True
            except ImportError as e:
                logger.info(f"HHEM 依赖未安装，将降级: {e}")
                self._load_failed = True
            except Exception as e:
                logger.warning(f"HHEM 模型加载失败: {e}")
                self._load_failed = True

    def _do_predict(self, premise: str, hypothesis: str) -> float:
        import torch

        max_input_length = 512
        input_text = f"premise: {premise} hypothesis: {hypothesis}"

        device = next(self._model.parameters()).device
        inputs = self._tokenizer(
            input_text,
            return_tensors="pt",
            max_length=max_input_length,
            truncation=True,
        ).to(device)

        with torch.no_grad():
            outputs = self._model(**inputs)
            logits = outputs.logits
            probs = torch.softmax(logits, dim=-1)
            # HHEM 输出 2 个 logits: [contradiction, entailment]
            score = probs[0][1].item()

        return score


class ChineseNLIValidator:
    """达摩院 StructBERT 中文 NLI 模型封装

    模型：damo/nlp_structbert_nli_chinese-base
    输出 3 分类：矛盾(0) / 蕴涵(1) / 中立(2)
    取蕴涵类别的概率作为忠实度分数。

    懒加载设计，线程安全。
    """

    _MODEL_NAME = "damo/nlp_structbert_nli_chinese-base"

    def __init__(self):
        self._model = None
        self._tokenizer = None
        self._loaded = False
        self._load_failed = False
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        if self._loaded:
            return True
        if self._load_failed:
            return False
        try:
            import torch
            import transformers

            return True
        except ImportError:
            return False

    def predict(self, premise: str, hypothesis: str) -> float:
        self._ensure_loaded()
        if self._model is None or self._tokenizer is None:
            return -1.0

        try:
            return self._do_predict(premise, hypothesis)
        except Exception as e:
            logger.warning(f"中文 NLI 推理失败: {e}")
            return -1.0

    def _ensure_loaded(self):
        if self._loaded or self._load_failed:
            return

        with self._lock:
            if self._loaded or self._load_failed:
                return

            try:
                import torch
                from transformers import AutoModelForSequenceClassification, AutoTokenizer

                logger.info(f"正在加载中文 NLI 模型: {self._MODEL_NAME}")
                self._tokenizer = AutoTokenizer.from_pretrained(self._MODEL_NAME)
                self._model = AutoModelForSequenceClassification.from_pretrained(self._MODEL_NAME)
                self._model.eval()

                device = "cuda" if torch.cuda.is_available() else "cpu"
                self._model = self._model.to(device)
                logger.info(f"中文 NLI 模型已加载到 {device}")

                self._loaded = True
            except ImportError as e:
                logger.info(f"中文 NLI 依赖未安装，将降级: {e}")
                self._load_failed = True
            except Exception as e:
                logger.warning(f"中文 NLI 模型加载失败: {e}")
                self._load_failed = True

    def _do_predict(self, premise: str, hypothesis: str) -> float:
        import torch

        max_input_length = 512

        device = next(self._model.parameters()).device
        inputs = self._tokenizer(
            premise,
            hypothesis,
            return_tensors="pt",
            max_length=max_input_length,
            truncation=True,
        ).to(device)

        with torch.no_grad():
            outputs = self._model(**inputs)
            logits = outputs.logits
            probs = torch.softmax(logits, dim=-1)
            # StructBERT NLI 中文版输出 3 分类：矛盾(0) / 蕴涵(1) / 中立(2)
            # 取蕴涵概率
            score = probs[0][1].item()

        return score


class EmbeddingSimilarityValidator:
    """基于 Embedding 余弦相似度的忠实度验证

    不需要额外模型，复用已有的 embedding 服务。
    适用于 NLI 模型不可用时的降级方案。

    原理：计算声明与原文的 embedding 余弦相似度，
    相似度高说明声明内容在原文中有依据。

    注意：相似度 ≠ 蕴含，但作为降级方案足够用。
    """

    def __init__(self):
        self._available = None

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available

        try:
            from Django_xm.apps.knowledge.services.embedding_service import get_embeddings

            embed = get_embeddings()
            self._available = embed is not None
        except Exception:
            self._available = False

        return self._available

    def predict(self, premise: str, hypothesis: str) -> float:
        """计算声明与原文的余弦相似度

        Returns:
            0-1 的相似度分数。如果 embedding 不可用，返回 -1.0。
        """
        try:
            import numpy as np

            from Django_xm.apps.knowledge.services.embedding_service import get_embeddings

            embed = get_embeddings()
            if embed is None:
                return -1.0

            # 获取 embedding
            premise_emb = embed.embed_query(premise)
            hypothesis_emb = embed.embed_query(hypothesis)

            if not premise_emb or not hypothesis_emb:
                return -1.0

            # 余弦相似度
            a = np.array(premise_emb)
            b = np.array(hypothesis_emb)
            norm_a = np.linalg.norm(a)
            norm_b = np.linalg.norm(b)

            if norm_a == 0 or norm_b == 0:
                return -1.0

            similarity = float(np.dot(a, b) / (norm_a * norm_b))

            # 将 [-1, 1] 映射到 [0, 1]
            return (similarity + 1) / 2

        except Exception as e:
            logger.debug(f"Embedding 相似度计算失败: {e}")
            return -1.0


class CompositeNLIValidator:
    """组合 NLI 验证器：根据语言自动选择模型

    策略：
    1. 检测输入语言（中文/英文）
    2. 英文 → HHEM-2.1-Open
    3. 中文 → StructBERT NLI 中文版
    4. 对应模型不可用 → Embedding 余弦相似度降级
    5. Embedding 也不可用 → 返回 -1.0（调用方回退到规则验证）
    """

    def __init__(self):
        self._hhem = HHEMValidator()
        self._cn_nli = ChineseNLIValidator()
        self._emb = EmbeddingSimilarityValidator()

    def is_available(self) -> bool:
        """至少有一个验证器可用即返回 True"""
        return self._hhem.is_available() or self._cn_nli.is_available() or self._emb.is_available()

    def predict(self, premise: str, hypothesis: str) -> float:
        """预测蕴含分数

        自动检测语言并选择对应模型。
        """
        lang = _detect_language(premise + " " + hypothesis)

        if lang == "zh":
            # 中文：优先 StructBERT NLI
            if self._cn_nli.is_available():
                score = self._cn_nli.predict(premise, hypothesis)
                if score >= 0:
                    return score

            # 降级到 Embedding 相似度
            if self._emb.is_available():
                score = self._emb.predict(premise, hypothesis)
                if score >= 0:
                    logger.info("中文 NLI 不可用，降级到 Embedding 相似度")
                    return score

            logger.info("中文 NLI 和 Embedding 均不可用")
            return -1.0

        else:
            # 英文：优先 HHEM
            if self._hhem.is_available():
                score = self._hhem.predict(premise, hypothesis)
                if score >= 0:
                    return score

            # 降级到 Embedding 相似度
            if self._emb.is_available():
                score = self._emb.predict(premise, hypothesis)
                if score >= 0:
                    logger.info("HHEM 不可用，降级到 Embedding 相似度")
                    return score

            logger.info("HHEM 和 Embedding 均不可用")
            return -1.0


# ── 模块级单例 ──────────────────────────────────────────────────────────────

_nli_instance: CompositeNLIValidator | None = None
_nli_lock = threading.Lock()


def get_nli_validator() -> CompositeNLIValidator:
    """获取组合 NLI 验证器单例"""
    global _nli_instance
    if _nli_instance is None:
        with _nli_lock:
            if _nli_instance is None:
                _nli_instance = CompositeNLIValidator()
    return _nli_instance


# ── 向后兼容 ──────────────────────────────────────────────────────────────


def get_hhem_validator() -> CompositeNLIValidator:
    """向后兼容：返回组合验证器（不再是纯 HHEM）"""
    return get_nli_validator()
