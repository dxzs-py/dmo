"""
上下文 Token 预算管理器

统筹分配各上下文块的 Token 配额，确保总消耗不超过模型限制的 90%（预留 10% 给输出）。
分区比例：system 15%、memory 10%、tools 10%、history 50%、state 5%、query 10%
超配额时返回超限标记，由调用方决定压缩或截断策略。
"""

import json
import threading
from dataclasses import dataclass
from typing import Any, ClassVar

from Django_xm.apps.context_manager.config import context_settings
from Django_xm.apps.context_manager.services.compression import TokenEstimator
from Django_xm.apps.context_manager.services.context_pruner import _is_protected
from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)

BUDGET_TEMPLATES: dict[str, dict[str, float]] = {
    "default": {"system": 0.15, "memory": 0.10, "tools": 0.10, "history": 0.50, "state": 0.05, "query": 0.10},
    "rag": {"system": 0.10, "memory": 0.15, "tools": 0.05, "history": 0.40, "state": 0.05, "query": 0.25},
    "coding": {"system": 0.20, "memory": 0.05, "tools": 0.15, "history": 0.45, "state": 0.05, "query": 0.10},
    "research": {"system": 0.10, "memory": 0.15, "tools": 0.10, "history": 0.35, "state": 0.05, "query": 0.25},
    "concise": {"system": 0.20, "memory": 0.05, "tools": 0.05, "history": 0.40, "state": 0.05, "query": 0.25},
}


def _load_custom_templates() -> dict[str, dict[str, float]]:
    """从配置加载自定义预算模板"""
    custom: dict[str, dict[str, float]] = {}
    try:
        templates_json = context_settings.budget_templates
        if templates_json and templates_json.strip():
            parsed = json.loads(templates_json)
            for name, ratios in parsed.items():
                # 验证比例总和为 1.0
                total = sum(ratios.values())
                if abs(total - 1.0) > 0.01:
                    logger.warning(f"自定义预算模板 '{name}' 比例总和为 {total:.4f}，跳过")
                    continue
                # 验证分区键
                valid_keys = set(BUDGET_TEMPLATES["default"].keys())
                if set(ratios.keys()) != valid_keys:
                    logger.warning(f"自定义预算模板 '{name}' 分区键不匹配，跳过")
                    continue
                custom[name] = ratios
    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"加载自定义预算模板失败: {e}")
    return custom


# 合并内置模板和自定义模板
_CUSTOM_TEMPLATES = _load_custom_templates()

SECTION_RATIOS: dict[str, float] = BUDGET_TEMPLATES["default"]

VALID_SECTIONS = frozenset(SECTION_RATIOS.keys())

OUTPUT_RESERVE_RATIO = 0.10


@dataclass
class BudgetTrimResult:
    """最终裁剪兜底结果（A12）：trimmed=True 表示实际发生了消息截断。"""

    trimmed: bool = False
    removed_count: int = 0
    original_tokens: int = 0
    trimmed_tokens: int = 0


def trim_messages_to_budget(
    messages: list[dict[str, Any]],
    budget_tokens: int,
    keep_recent: int = 6,
    model_name: str = "",
) -> tuple[list[dict[str, Any]], BudgetTrimResult]:
    """最终裁剪兜底（A12）：压缩/相关性筛选后仍超预算时，按预算截断最旧消息。

    固定保留（不参与裁剪）：
    - 结构化保护消息：system 角色 / pinned / memory_tier=long_term（复用
      context_pruner._is_protected，与压缩层分层语义一致）；
    - 最近 keep_recent 条消息（时序上的近因窗口，无论是否受保护）。

    裁剪顺序：从最旧的非保留消息开始逐条移除，直至预算内或无可裁剪消息。
    protected 消息本身即超预算时裁无可裁（trimmed=False），由调用方决定后续。

    model_name（fix-review-round2 4.4）：透传给 TokenEstimator 做 token 校准，
    调用链（manager.allocate_budget → 本函数）在 allocate 之后取
    ``resolved_model``；空串时估算器退回 cl100k_base 近似（原行为）。

    Returns:
        (裁剪后消息列表, BudgetTrimResult)
    """
    result = BudgetTrimResult()
    if not messages:
        return messages, result

    result.original_tokens = TokenEstimator.estimate_dict_messages(messages, model_name)
    if budget_tokens <= 0 or result.original_tokens <= budget_tokens:
        return messages, result

    keep_recent = max(0, keep_recent)
    recent_start = max(0, len(messages) - keep_recent)
    per_message_tokens = [TokenEstimator.estimate_dict_messages([msg], model_name) for msg in messages]
    keep_flags = [
        _is_protected(msg) or i >= recent_start for i, msg in enumerate(messages)
    ]

    total_tokens = result.original_tokens
    removed = 0
    for i in range(len(messages)):  # 最旧优先
        if total_tokens <= budget_tokens:
            break
        if keep_flags[i]:
            continue
        total_tokens -= per_message_tokens[i]
        keep_flags[i] = False
        removed += 1

    if removed == 0:
        return messages, result

    result.trimmed = True
    result.removed_count = removed
    result.trimmed_tokens = total_tokens
    logger.info(
        f"Token 预算最终裁剪: 移除 {removed} 条最旧消息, "
        f"{result.original_tokens} -> {total_tokens} tokens (budget={budget_tokens})"
    )
    trimmed_messages = [msg for i, msg in enumerate(messages) if keep_flags[i]]
    return trimmed_messages, result


@dataclass
class BudgetAllocation:
    section: str
    budget: int = 0
    used: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.budget - self.used)


@dataclass
class BudgetCheckResult:
    within_budget: bool
    section: str
    budget: int
    used: int
    requested: int
    over_by: int = 0


class TokenBudgetManager:
    _custom_templates: ClassVar[dict[str, dict[str, float]]] = {}

    def __init__(self) -> None:
        self._model_name: str | None = None
        self._model_limit: int = 0
        self._total_budget: int = 0
        self._allocations: dict[str, BudgetAllocation] = {}
        self._current_template: str = "default"
        self._lock = threading.Lock()

    def allocate(self, model_name: str, template: str = "default") -> None:
        with self._lock:
            self._current_template = template
            ratios = self._resolve_template(template)
            self._model_name = model_name
            self._model_limit = TokenEstimator.get_model_limit(model_name)
            self._total_budget = int(self._model_limit * (1 - OUTPUT_RESERVE_RATIO))
            self._allocations = {}
            for section, ratio in ratios.items():
                self._allocations[section] = BudgetAllocation(
                    section=section,
                    budget=int(self._total_budget * ratio),
                    used=0,
                )
            logger.info(
                f"Token 预算初始化: model={model_name}, "
                f"limit={self._model_limit}, "
                f"total_budget={self._total_budget}, "
                f"template={template}"
            )

    @classmethod
    def _resolve_template(cls, template: str) -> dict[str, float]:
        merged = {**BUDGET_TEMPLATES, **_CUSTOM_TEMPLATES, **cls._custom_templates}
        if template not in merged:
            logger.warning(f"未知预算模板 '{template}'，回退到 'default'")
            return BUDGET_TEMPLATES["default"]
        return merged[template]

    def check(self, section_name: str, token_count: int) -> BudgetCheckResult:
        allocation = self._allocations.get(section_name)
        if allocation is None:
            return BudgetCheckResult(
                within_budget=False,
                section=section_name,
                budget=0,
                used=0,
                requested=token_count,
                over_by=token_count,
            )
        projected = allocation.used + token_count
        over_by = max(0, projected - allocation.budget)
        return BudgetCheckResult(
            within_budget=projected <= allocation.budget,
            section=section_name,
            budget=allocation.budget,
            used=allocation.used,
            requested=token_count,
            over_by=over_by,
        )

    def get_section_budget(self, section_name: str) -> int:
        """查询分区预算上限（A12 最终裁剪兜底的目标预算）；分区未分配时返回 0"""
        with self._lock:
            allocation = self._allocations.get(section_name)
            return allocation.budget if allocation is not None else 0

    def get_usage(self) -> dict[str, dict[str, int]]:
        with self._lock:
            result: dict[str, dict[str, int]] = {}
            for section, allocation in self._allocations.items():
                result[section] = {
                    "budget": allocation.budget,
                    "used": allocation.used,
                    "remaining": allocation.remaining,
                }
            return result

    def record_usage(self, section_name: str, token_count: int) -> BudgetCheckResult:
        with self._lock:
            check_result = self.check(section_name, token_count)
            allocation = self._allocations.get(section_name)
            if allocation is not None:
                allocation.used += token_count
            return check_result

    def reset_usage(self, section_name: str | None = None) -> None:
        with self._lock:
            if section_name is not None:
                allocation = self._allocations.get(section_name)
                if allocation is not None:
                    allocation.used = 0
            else:
                for allocation in self._allocations.values():
                    allocation.used = 0

    @property
    def model_name(self) -> str | None:
        return self._model_name

    @property
    def model_limit(self) -> int:
        return self._model_limit

    @property
    def total_budget(self) -> int:
        return self._total_budget
