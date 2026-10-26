"""引用真实性校验（Phase 2）：用 LLM Judge 判断答案中的 [n] 是否被来源支持。

与 ``_strip_invalid_citations`` 的分工：
- 后者只校验**编号越界**（模型编造 [99]）；
- 本模块校验**语义支撑**（编号存在但来源并不支持该句）。

策略（误判保护优先）：
- 只抽取含 ``[n]`` 的句子（最多 ``max_sentences`` 条），一次批量交给 LLM；
- 只有**明确 supported=false** 才移除对应引用编号，**不改写正文**；
- LLM 不可用 / 超时 / JSON 解析失败 → 原答案原样返回，``skipped=True``；
- 移除后可能出现「…」句末无引用，这是预期行为（宁可少引用，不可假引用）。

流式链路不使用本模块（已发送内容不可篡改），只在非流式 ``query_strict_rag`` 生效。
"""

from __future__ import annotations

import json
import re
from typing import Any

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)

CITATION_VERIFY_PROMPT = """请判断下列「陈述」是否能被对应「资料」直接支持。

判断标准：
- supported=true：资料中有明确依据（可直接引用或等价改写）；
- supported=false：资料未提及、与陈述矛盾、或仅能弱相关推测。

只输出 JSON 数组，不要解释，不要 markdown 代码块：
[{{"id": 1, "supported": true}}, {{"id": 2, "supported": false}}]

共 {count} 条：
{items}
"""

_CITATION_RE = re.compile(r"\[(\d+)\]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?])\s*|\n+")
_SOURCE_PREVIEW_CHARS = 800


def _content_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            item.get("text", "") if isinstance(item, dict) else str(item) for item in content
        )
    return str(content or "")


def _extract_claims(answer: str, max_sentences: int) -> list[tuple[str, list[int]]]:
    """抽取含 [n] 的句子及其引用编号。"""
    claims: list[tuple[str, list[int]]] = []
    for sentence in _SENTENCE_SPLIT_RE.split(answer or ""):
        text = sentence.strip()
        if not text:
            continue
        numbers = sorted({int(n) for n in _CITATION_RE.findall(text)})
        if not numbers:
            continue
        claims.append((text, numbers))
        if len(claims) >= max_sentences:
            break
    return claims


def _build_items(claims: list[tuple[str, list[int]]], sources: list[dict]) -> tuple[list[str], list[int]]:
    """构造 (prompt 条目, 有效 id 列表)；越界编号不参与。"""
    items: list[str] = []
    valid_ids: list[int] = []
    for idx, (sentence, numbers) in enumerate(claims, 1):
        refs = [
            f"[{n}] {str(sources[n - 1].get('content', ''))[:_SOURCE_PREVIEW_CHARS]}"
            for n in numbers
            if 1 <= n <= len(sources)
        ]
        if not refs:
            continue
        valid_ids.append(idx)
        items.append(f"{idx}. 陈述：{sentence[:500]}\n资料：\n" + "\n".join(refs))
    return items, valid_ids


def _parse_verdicts(text: str) -> dict[int, bool] | None:
    match = re.search(r"\[.*\]", text or "", re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group())
    except ValueError:
        return None
    if not isinstance(data, list):
        return None

    verdicts: dict[int, bool] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        supported = item.get("supported")
        if isinstance(supported, str):
            supported = supported.strip().lower() == "true"
        verdicts[idx] = bool(supported)
    return verdicts or None


def _resolve_llm(llm=None):
    """解析 Judge 模型：默认主模型（效果优先），可配置回退 helper。"""
    if llm is not None:
        return llm

    try:
        from Django_xm.apps.knowledge.rag_settings import get_rag_runtime_settings

        mode = get_rag_runtime_settings()["citation_verify_model_mode"]
    except Exception:
        mode = "main"

    if mode == "main":
        try:
            from Django_xm.apps.knowledge.config import get_chat_model

            return get_chat_model(streaming=False)
        except Exception as e:
            logger.warning(f"引用校验主模型不可用: {e}")
            return None

    try:
        from Django_xm.apps.knowledge.config import get_helper_model

        helper = get_helper_model()
        if helper is not None:
            return helper
    except Exception:  # noqa: S110  # 可选模型解析失败，继续尝试下一来源
        pass
    try:
        from Django_xm.apps.knowledge.config import get_chat_model

        return get_chat_model(streaming=False)
    except Exception:
        return None


def verify_answer_citations(
    answer: str,
    sources: list[dict],
    *,
    llm=None,
    max_sentences: int = 8,
) -> tuple[str, dict[str, Any]]:
    """校验答案引用是否被来源支持，移除不被支持的编号。

    Args:
        answer: 已通过编号越界校验的答案
        sources: ``_extract_sources`` 产出的来源列表（content 为片段预览）
        llm: Judge 模型，None 时 helper 优先、主模型兜底
        max_sentences: 单次校验句子上限

    Returns:
        (处理后答案, {"checked": n, "unsupported": m, "skipped": bool})
    """
    stats: dict[str, Any] = {"checked": 0, "unsupported": 0, "skipped": False}
    if not answer or not sources:
        return answer, stats

    claims = _extract_claims(answer, max_sentences)
    if not claims:
        return answer, stats

    items, valid_ids = _build_items(claims, sources)
    if not items:
        return answer, stats

    judge = _resolve_llm(llm)
    if judge is None:
        stats["skipped"] = True
        return answer, stats

    try:
        response = judge.invoke(
            [{"role": "user", "content": CITATION_VERIFY_PROMPT.format(count=len(items), items="\n\n".join(items))}]
        )
        verdicts = _parse_verdicts(_content_text(response))
    except Exception as e:
        logger.warning(f"引用真实性校验失败（保留原答案）: {e}")
        stats["skipped"] = True
        return answer, stats

    if verdicts is None:
        logger.warning("引用真实性校验输出无法解析（保留原答案）")
        stats["skipped"] = True
        return answer, stats

    remove_map: dict[str, set[int]] = {}
    for idx in valid_ids:
        sentence, numbers = claims[idx - 1]
        stats["checked"] += len(numbers)
        if verdicts.get(idx) is False:
            remove_map.setdefault(sentence, set()).update(numbers)
            stats["unsupported"] += len(numbers)

    if not remove_map:
        return answer, stats

    for sentence, numbers in remove_map.items():
        cleaned = sentence
        for number in numbers:
            cleaned = cleaned.replace(f"[{number}]", "")
        answer = answer.replace(sentence, cleaned)

    logger.info(
        f"引用真实性校验: checked={stats['checked']} unsupported={stats['unsupported']}"
    )
    return answer, stats
