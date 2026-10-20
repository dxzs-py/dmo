"""
上下文 Token 预算管理器

统筹分配各上下文块的 Token 配额，确保总消耗不超过模型限制的 90%（预留 10% 给输出）。
分区比例（default 模板，实测校准后）：system_static 5%、system_dynamic 3%、
memory 10%、tools 8%、history 59%、state 5%、query 10%。
超配额时返回超限标记，由调用方决定压缩或截断策略。

分区键语义（原 "system" 单键拆分为静态/动态两类独立预算，禁止混用）：
- system_static：静态系统提示词段（base prompt + 技能 L1 元数据，静态可缓存）。
  运行时记录点为模型调用侧捕获的 request.system_prompt（middleware
  _capture_system_prompt_tokens，round3 2.10 全量预算口径），经
  record_usage("system_static", n) 记入；上下文工程链路内不可见该段。
- system_dynamic：记忆注入段（知识图谱上下文 + 跨会话摘要，即原 "system"
  分区实际记录的 get_injection_context 产物），随轮次变化，不可缓存。
- memory：build_prompt_context 流的 <memory> 块注入（文档记忆等），与
  system_dynamic 为不同流程的不同 XML 块，均为动态注入。
- tools：工具使用说明 + MCP 工具清单（<tools> 块），保持独立配额——
  MCP server 增多时该段显著膨胀，超限告警独立于静态段。
- history / state / query：会话历史 / 运行状态 / 用户查询，语义不变。
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

# 比例经 .trae/tmp/harness_budget_calibration.py 实测校准（128K 档，
# total_budget=115200；校准推导见 .trae/tmp/harness_budget_calibration_result.md）：
# - system_static：实测 708 tokens（agent 模式 base prompt + 3 个真实技能包，
#   占总预算 0.6%）；0.10→0.05，保留 8× 余量覆盖 ~140 个技能包的病态增长。
# - system_dynamic：实测样本 224 tokens，现实最坏 ~1.3% 总预算；0.05→0.03，
#   约为最坏情形 2 倍，超限即告警。
# - tools：实测 MCP=10 servers 时 3638 tokens（31.6% of 0.10 配额），每 server
#   约 +146 tokens（描述 80 字符截断为代码级上限）；0.10→0.08，触顶点 ≈48
#   servers。"tools 10% 不够"的担忧被实测否定。
# - memory：文档记忆随用户上传量无上界，维持 0.10 不动。
# - history：实际占比最大（20 轮 ≈73%）且是压缩/裁剪管线唯一可治理段，
#   承接上方释放的 0.09：0.50→0.59。
BUDGET_TEMPLATES: dict[str, dict[str, float]] = {
    "default": {
        "system_static": 0.05,
        "system_dynamic": 0.03,
        "memory": 0.10,
        "tools": 0.08,
        "history": 0.59,
        "state": 0.05,
        "query": 0.10,
    },
    # rag/research/coding/concise：同口径校准——静态侧收紧、差额归 history，
    # 各场景特有配额（rag/research 的 query+memory、coding 的 tools）保留现值。
    "rag": {
        "system_static": 0.04,
        "system_dynamic": 0.02,
        "memory": 0.15,
        "tools": 0.05,
        "history": 0.44,
        "state": 0.05,
        "query": 0.25,
    },
    "coding": {
        "system_static": 0.12,
        "system_dynamic": 0.04,
        "memory": 0.05,
        "tools": 0.15,
        "history": 0.49,
        "state": 0.05,
        "query": 0.10,
    },
    "research": {
        "system_static": 0.04,
        "system_dynamic": 0.02,
        "memory": 0.15,
        "tools": 0.06,
        "history": 0.43,
        "state": 0.05,
        "query": 0.25,
    },
    "concise": {
        "system_static": 0.12,
        "system_dynamic": 0.04,
        "memory": 0.05,
        "tools": 0.05,
        "history": 0.44,
        "state": 0.05,
        "query": 0.25,
    },
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

# 比例校准记录（实测 → 结论，禁止无依据调整）：由
# .trae/tmp/harness_budget_calibration.py 在真实来源样本上测得，
# 数值与推导见 .trae/tmp/harness_budget_calibration_result.md。
# - initial（拆分前现值按静态:动态 ≈ 2:1 初始拆分，未实测）：
#   default=static 0.10/dynamic 0.05；rag=research 0.07/0.03；
#   coding=concise 0.15/0.05。
# - calibrated（status=calibrated，2026-10-18 实测回填）：
#   default=static 0.05/dynamic 0.03/tools 0.08/history 0.59；
#   rag=0.04/0.02/history 0.44；research=0.04/0.02/tools 0.06/history 0.43；
#   coding=concise 0.12/0.04/history 0.49（coding tools 0.15、concise tools
#   0.05 维持）。静态侧收紧的差额全部归 history。
CALIBRATION_NOTES: dict[str, str] = {
    "status": "calibrated",
    "source": ".trae/tmp/harness_budget_calibration_result.md（2026-10-18 实测，"
    "128K 档 total_budget=115200；实测值：system_static=708、tools=2175~3638"
    "（每 server +146）、system_dynamic 样本=224、history≈474-483/轮）",
}


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
        ratios = merged[template]
        # 分区键一致性唯一权威门禁（含类级注入路径，_load_custom_templates 的
        # 加载期校验不覆盖此处）：旧键（如 "system"）/缺键模板一律拒绝回退，
        # 不留兼容读取。
        if set(ratios.keys()) != set(VALID_SECTIONS):
            logger.warning(
                f"预算模板 '{template}' 分区键不匹配（期望 {sorted(VALID_SECTIONS)}），回退到 'default'"
            )
            return BUDGET_TEMPLATES["default"]
        return ratios

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
