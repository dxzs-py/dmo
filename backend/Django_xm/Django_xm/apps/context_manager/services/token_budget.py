"""
上下文 Token 预算管理器

统筹分配各上下文块的 Token 配额，确保总消耗不超过模型限制的 90%（预留 10% 给输出）。
分区比例：system 15%、memory 10%、tools 10%、history 50%、state 5%、query 10%
超配额时返回超限标记，由调用方决定压缩或截断策略。
"""

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import threading

from Django_xm.apps.context_manager.services.compression import TokenEstimator
from Django_xm.apps.context_manager.config import context_settings, get_logger

logger = get_logger(__name__)

BUDGET_TEMPLATES: Dict[str, Dict[str, float]] = {
    "default": {"system": 0.15, "memory": 0.10, "tools": 0.10, "history": 0.50, "state": 0.05, "query": 0.10},
    "rag": {"system": 0.10, "memory": 0.15, "tools": 0.05, "history": 0.40, "state": 0.05, "query": 0.25},
    "coding": {"system": 0.20, "memory": 0.05, "tools": 0.15, "history": 0.45, "state": 0.05, "query": 0.10},
    "research": {"system": 0.10, "memory": 0.15, "tools": 0.10, "history": 0.35, "state": 0.05, "query": 0.25},
    "concise": {"system": 0.20, "memory": 0.05, "tools": 0.05, "history": 0.40, "state": 0.05, "query": 0.25},
}


def _load_custom_templates() -> Dict[str, Dict[str, float]]:
    """从配置加载自定义预算模板"""
    custom: Dict[str, Dict[str, float]] = {}
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

SECTION_RATIOS: Dict[str, float] = BUDGET_TEMPLATES["default"]

VALID_SECTIONS = frozenset(SECTION_RATIOS.keys())

OUTPUT_RESERVE_RATIO = 0.10


class ContextEfficiencyMetrics:
    """上下文使用效率度量"""

    def __init__(self) -> None:
        self._total_injected_tokens: int = 0       # 注入的总token数
        self._total_retrieved_tokens: int = 0       # 检索结果token数
        self._total_compressed_tokens: int = 0      # 压缩后保留token数
        self._compression_count: int = 0            # 压缩次数
        self._retrieval_hit_count: int = 0          # 检索命中次数
        self._retrieval_miss_count: int = 0         # 检索未命中次数
        # 压缩前总量（用于计算压缩保留率）
        self._total_original_tokens_before_compression: int = 0

    def record_injection(self, injected_tokens: int, retrieved_tokens: int = 0) -> None:
        """记录上下文注入"""
        self._total_injected_tokens += injected_tokens
        self._total_retrieved_tokens += retrieved_tokens

    def record_compression(self, original_tokens: int, compressed_tokens: int) -> None:
        """记录压缩"""
        self._total_original_tokens_before_compression += original_tokens
        self._total_compressed_tokens += compressed_tokens
        self._compression_count += 1

    def record_retrieval(self, hit: bool) -> None:
        """记录检索命中/未命中"""
        if hit:
            self._retrieval_hit_count += 1
        else:
            self._retrieval_miss_count += 1

    @property
    def compression_retention_rate(self) -> float:
        """压缩保留率：压缩后/压缩前"""
        if self._total_original_tokens_before_compression <= 0:
            return 0.0
        return self._total_compressed_tokens / self._total_original_tokens_before_compression

    @property
    def retrieval_hit_rate(self) -> float:
        """检索命中率"""
        total = self._retrieval_hit_count + self._retrieval_miss_count
        if total <= 0:
            return 0.0
        return self._retrieval_hit_count / total

    @property
    def effective_info_ratio(self) -> float:
        """有效信息比：检索结果/总注入（近似）"""
        if self._total_injected_tokens <= 0:
            return 0.0
        return self._total_retrieved_tokens / self._total_injected_tokens

    def get_summary(self) -> dict[str, float]:
        """获取效率摘要"""
        return {
            "compression_retention_rate": round(self.compression_retention_rate, 4),
            "retrieval_hit_rate": round(self.retrieval_hit_rate, 4),
            "effective_info_ratio": round(self.effective_info_ratio, 4),
            "total_injected_tokens": float(self._total_injected_tokens),
            "total_retrieved_tokens": float(self._total_retrieved_tokens),
            "total_compressed_tokens": float(self._total_compressed_tokens),
            "compression_count": float(self._compression_count),
            "retrieval_hit_count": float(self._retrieval_hit_count),
            "retrieval_miss_count": float(self._retrieval_miss_count),
        }


@dataclass
class BudgetAllocation:
    section: str
    budget: int = 0
    used: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.budget - self.used)

    @property
    def usage_ratio(self) -> float:
        if self.budget <= 0:
            return 0.0
        return self.used / self.budget

    @property
    def is_over(self) -> bool:
        return self.used > self.budget


@dataclass
class BudgetCheckResult:
    within_budget: bool
    section: str
    budget: int
    used: int
    requested: int
    over_by: int = 0


class TokenBudgetManager:

    _custom_templates: Dict[str, Dict[str, float]] = {}

    def __init__(self) -> None:
        self._model_name: Optional[str] = None
        self._model_limit: int = 0
        self._total_budget: int = 0
        self._allocations: Dict[str, BudgetAllocation] = {}
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
    def _resolve_template(cls, template: str) -> Dict[str, float]:
        merged = {**BUDGET_TEMPLATES, **_CUSTOM_TEMPLATES, **cls._custom_templates}
        if template not in merged:
            logger.warning(f"未知预算模板 '{template}'，回退到 'default'")
            return BUDGET_TEMPLATES["default"]
        return merged[template]

    @classmethod
    def list_templates(cls) -> List[str]:
        merged = {**BUDGET_TEMPLATES, **_CUSTOM_TEMPLATES, **cls._custom_templates}
        return sorted(merged.keys())

    @classmethod
    def register_template(cls, name: str, ratios: Dict[str, float]) -> None:
        total = sum(ratios.values())
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"预算模板 '{name}' 比例总和为 {total:.4f}，必须等于 1.0")
        valid_keys = set(BUDGET_TEMPLATES["default"].keys())
        if set(ratios.keys()) != valid_keys:
            raise ValueError(f"预算模板 '{name}' 必须包含且仅包含分区: {sorted(valid_keys)}")
        cls._custom_templates[name] = ratios
        logger.info(f"注册自定义预算模板: {name}")

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

    def get_budget(self, section_name: str) -> int:
        with self._lock:
            allocation = self._allocations.get(section_name)
            return allocation.budget if allocation else 0

    def get_usage(self) -> Dict[str, Dict[str, int]]:
        with self._lock:
            result: Dict[str, Dict[str, int]] = {}
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

    def reset_usage(self, section_name: Optional[str] = None) -> None:
        with self._lock:
            if section_name is not None:
                allocation = self._allocations.get(section_name)
                if allocation is not None:
                    allocation.used = 0
            else:
                for allocation in self._allocations.values():
                    allocation.used = 0

    @property
    def model_name(self) -> Optional[str]:
        return self._model_name

    @property
    def model_limit(self) -> int:
        return self._model_limit

    @property
    def total_budget(self) -> int:
        return self._total_budget

    @property
    def total_used(self) -> int:
        with self._lock:
            return sum(a.used for a in self._allocations.values())
