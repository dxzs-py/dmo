"""Skill 步骤级审批门禁（fix-review-round3 1.2）

背景：SkillBaseTool（skill_{name} 管道工具）在管道内直接 ``tool.ainvoke``
执行子工具，内层 shell_exec 等副作用步骤此前绕过 ApprovalMiddleware
（外层 skill_{name} 调用虽有 UnregisteredToolApprovalPolicy 审批，内层步骤
不经任何审批——R3 已核实的审批逃逸面）。

方案：把"工具名 → 风险判定"抽成本模块可复用函数，ApprovalMiddleware 与
Skill 管道共用同一策略表与判定逻辑；需审批的 Skill 步骤经 LangGraph
``interrupt()`` 发起审批中断，复用既有全链路恢复机制：

- 工具内 interrupt 已验证可行（ToolNode 对工具内 GraphInterrupt 显式
  re-raise，langgraph 官方支持场景 1）；
- 中断 payload 采用与 ApprovalMiddleware 完全相同的批量格式
  （``{"_approval": True, "requests": [...]}``），common/approval_parser
  解析、Approval DB 记录、前端审批 UI、Command(resume) 恢复全部复用；
- resume 协议：``Command(resume={Interrupt.id: {tool_call_id: decision}})``
  → ``interrupt()`` 返回 ``{tool_call_id: decision}`` dict（与 middleware
  消费格式一致）。

恢复重放语义（选型依据，tasks.md 1.2）：
- 图恢复时工具节点从头重放，管道内先前已执行的步骤会重新执行：
  SAFE 级步骤重放无害（定义即只读/自动通过）；经审批的 CONTROLLED/HIGH
  步骤以结果缓存去重（键 = 确定性 step interrupt_id，跨重放稳定），
  防止副作用重复；缓存随 Django cache TTL 过期，进程重启场景可能重放
  执行一次已审批步骤（宁可少执行不可未审批执行，残余风险已评估）。
- 拒绝/超时/门禁异常一律 fail-closed 终止技能（返回明确错误文案）。
"""

import logging
import threading
import uuid
from typing import Any, NamedTuple

from django.core.cache import cache

from Django_xm.common.risk_levels import RiskLevel

logger = logging.getLogger(__name__)

# 已执行步骤结果缓存：键前缀与 TTL（恢复重放去重；TTL 覆盖审批等待 +
# 后续 interrupt 的合理时长）
SKILL_STEP_CACHE_PREFIX = "skill_step_exec_result:"
SKILL_STEP_CACHE_TTL = 6 * 3600

# 缓存缺失哨兵（区分"未缓存"与"缓存值为 None"）
_CACHE_MISS = object()

_policy_registry: dict[str, Any] | None = None
_registry_lock = threading.Lock()


class SkillStepGateResult(NamedTuple):
    """Skill 步骤门禁判定结果。"""

    allowed: bool
    """是否允许执行该步骤"""
    message: str
    """allowed=False 时的拒绝/超时/门禁失败说明（进技能终止文案）"""
    step_interrupt_id: str
    """确定性步骤 interrupt_id（审批请求与结果缓存共用）"""
    cached_result: Any | None
    """恢复重放命中已执行缓存时的步骤结果（跳过重复执行）；None 表示需执行"""
    requires_result_cache: bool
    """本步骤经过审批门禁（True 时调用方执行成功后应写结果缓存）"""


def get_default_policy_registry() -> dict[str, Any]:
    """默认审批策略注册表（单例，与 ApprovalMiddleware._default_policies 同源）。"""
    global _policy_registry
    with _registry_lock:
        if _policy_registry is None:
            from .middleware import ApprovalMiddleware

            _policy_registry = {
                p.tool_name: p for p in ApprovalMiddleware._default_policies() if p.tool_name
            }
        return _policy_registry


def resolve_tool_policy(tool_name: str, policies: dict[str, Any] | None = None) -> Any | None:
    """工具名 → 审批策略（R3 1.2：middleware 与 Skill 步骤门禁共用，同源判定）。

    - 系统编排工具（SYSTEM_ORCHESTRATION_TOOLS）返回 None：非审批类
      interrupt，调用方按编排语义放行（middleware 侧注册 ctx + 发布 PENDING）；
    - 未注册工具返回 UnregisteredToolApprovalPolicy 兜底（fail-closed
      CONTROLLED，需审批）；
    - 其余返回注册的策略实例。

    Args:
        tool_name: 工具名
        policies: 自定义策略表（middleware 传入实例表）；None 用默认注册表
    """
    from .middleware import SYSTEM_ORCHESTRATION_TOOLS
    from .policies import UnregisteredToolApprovalPolicy

    if tool_name in SYSTEM_ORCHESTRATION_TOOLS:
        return None
    registry = policies if policies is not None else get_default_policy_registry()
    policy = registry.get(tool_name)
    if policy is None:
        policy = UnregisteredToolApprovalPolicy(tool_name)
    return policy


def build_skill_step_interrupt_id(skill_name: str, step_idx: int) -> str:
    """构建确定性步骤 interrupt_id（跨恢复重放稳定，与真实 tool_call_id 空间隔离）。

    种子优先级：langgraph 任务 id（工具执行所在任务的 __pregel_task_id，
    已验证跨重放稳定）→ 随机兜底（彻底图外调用，仅本进程内去重）。
    """
    seed = ""
    try:
        from langgraph.config import get_config

        cfg = get_config()
        seed = str((cfg.get("configurable") or {}).get("__pregel_task_id") or "")
    except Exception:
        seed = ""
    if not seed:
        seed = uuid.uuid4().hex
    return f"skillstep-{skill_name}-{step_idx}-{seed}"


def _extract_session_id() -> str:
    """从 langgraph 运行期 configurable 提取会话 id（审批记录归属路由用）。"""
    try:
        from langgraph.config import get_config

        cfg = get_config()
        configurable = cfg.get("configurable") or {}
        return str(
            configurable.get("chat_session_id")
            or configurable.get("session_id")
            or configurable.get("thread_id")
            or ""
        )
    except Exception:
        return ""


def _extract_decision(decisions: Any, step_interrupt_id: str) -> Any:
    """从 interrupt() 返回值提取本步骤决策（对齐 middleware._normalize_decisions 口径）。

    支持格式：{tool_call_id: bool|"timeout"}（审批批次恢复协议）、
    bool（整批统一）。未知格式按拒绝处理（fail-closed）。
    """
    from .timeout_handler import TIMEOUT_DECISION

    if isinstance(decisions, dict):
        if step_interrupt_id in decisions:
            return decisions[step_interrupt_id]
        # 包装格式 {langgraph_resume_id: {tool_call_id: decision}}：
        # 单键且值是 dict 时解包（与 middleware 解包语义一致）
        if len(decisions) == 1:
            only_value = next(iter(decisions.values()))
            if isinstance(only_value, dict) and step_interrupt_id in only_value:
                return only_value[step_interrupt_id]
        return False
    if isinstance(decisions, bool):
        return decisions
    if decisions == TIMEOUT_DECISION:
        return TIMEOUT_DECISION
    return False


async def gate_skill_step_tool_call(
    tool_name: str,
    args: dict,
    *,
    skill_name: str,
    step_idx: int,
) -> SkillStepGateResult:
    """Skill 步骤审批门禁：工具名 → 风险判定 → 需审批则 LangGraph 审批中断（R3 1.2）。

    与 ApprovalMiddleware 共用策略表（resolve_tool_policy）与风险判定
    （policy.assess_risk，含子 agent 加权）。判定结果：

    - SAFE：直接放行（只读步骤不弹审批）；
    - CONTROLLED/HIGH：以批量格式 payload 发起 ``interrupt()``——图挂起，
      既有审批 UI 展示（请求携带 skill_name/step_idx 上下文），用户决策后
      ``Command(resume)`` 恢复、``interrupt()`` 返回决策：
      批准 → 查已执行缓存（命中则跳过执行返回缓存结果）；拒绝/超时 → 不放行。

    Args:
        tool_name: 步骤要调用的子工具名
        args: 已解析的步骤参数（模板插值后的实际参数，风险判定基于真实参数）
        skill_name: 所属技能名（审批上下文展示）
        step_idx: 步骤序号（审批上下文展示）

    Returns:
        SkillStepGateResult
    """
    policy = resolve_tool_policy(tool_name)
    if policy is None:
        # 系统编排工具：Skill 管道正常不会编排 wait_for_subagent，防御性放行
        return SkillStepGateResult(True, "", "", None, False)

    step_interrupt_id = build_skill_step_interrupt_id(skill_name, step_idx)

    # 子 agent 加权：复用 middleware 的子代理上下文提取（工具内可读取运行期 config）
    try:
        from .middleware import ApprovalMiddleware

        subagent_context = ApprovalMiddleware._extract_subagent_context(None, None)
    except Exception:
        subagent_context = None

    risk_level = policy.assess_risk(args, subagent_context=subagent_context)
    if risk_level == RiskLevel.SAFE:
        return SkillStepGateResult(True, "", step_interrupt_id, None, False)

    # 需审批：构建与 middleware 完全同构的批量格式审批请求
    # （parser 按请求字段生成 approval_data；skill_name/step_idx 由
    # parse_approval_interrupt 显式透传到前端与 Approval.extra）
    request = {
        "tool_name": tool_name,
        "tool_call_id": step_interrupt_id,
        "session_id": _extract_session_id(),
        "title": f"技能 {skill_name} 步骤 {step_idx} 调用 {tool_name}",
        "description": (
            f"技能“{skill_name}”的第 {step_idx} 步请求执行工具 {tool_name}。"
            f"{policy.build_description(args)}"
        ),
        "operation": policy.build_operation_desc(args),
        "danger_level": policy.assess_danger(args),
        "risk_level": risk_level.value,
        "args": args,
        "skill_name": skill_name,
        "step_idx": step_idx,
    }

    logger.info(
        f"[StepGate] Skill 步骤需审批: skill={skill_name}, step={step_idx}, "
        f"tool={tool_name}, risk={risk_level.value}, step_interrupt_id={step_interrupt_id}"
    )

    try:
        from langgraph.types import interrupt

        decisions = interrupt({"_approval": True, "requests": [request]})
    except Exception as gate_err:
        from langgraph.errors import GraphInterrupt

        if isinstance(gate_err, GraphInterrupt):
            raise  # 图挂起语义：必须向上传播（checkpoint 记录 + 恢复重放）
        # 门禁不可用（图外调用/无 checkpointer 等）→ fail-closed 拒绝执行
        logger.error(
            f"[StepGate] Skill 步骤审批门禁不可用（fail-closed）: skill={skill_name}, "
            f"step={step_idx}, tool={tool_name}, err={type(gate_err).__name__}: {gate_err}"
        )
        return SkillStepGateResult(
            False,
            f"审批门禁不可用（{type(gate_err).__name__}），已拒绝执行该步骤（fail-closed）",
            step_interrupt_id,
            None,
            False,
        )

    from .timeout_handler import TIMEOUT_DECISION

    decision = _extract_decision(decisions, step_interrupt_id)
    if decision is True:
        # 批准：查已执行结果缓存（恢复重放去重，防副作用重复）
        cached = cache.get(f"{SKILL_STEP_CACHE_PREFIX}{step_interrupt_id}", _CACHE_MISS)
        if cached is not _CACHE_MISS:
            logger.info(
                f"[StepGate] Skill 步骤重放命中已执行缓存，跳过重复执行: "
                f"skill={skill_name}, step={step_idx}, tool={tool_name}"
            )
            return SkillStepGateResult(True, "", step_interrupt_id, cached, False)
        return SkillStepGateResult(True, "", step_interrupt_id, None, True)

    if decision == TIMEOUT_DECISION:
        logger.warning(
            f"[StepGate] Skill 步骤审批超时，技能终止: skill={skill_name}, step={step_idx}, tool={tool_name}"
        )
        return SkillStepGateResult(
            False, "审批超时，未获执行许可", step_interrupt_id, None, False
        )

    logger.warning(
        f"[StepGate] Skill 步骤被拒绝，技能终止: skill={skill_name}, step={step_idx}, tool={tool_name}"
    )
    return SkillStepGateResult(False, "用户拒绝执行该步骤", step_interrupt_id, None, False)


def cache_skill_step_result(step_interrupt_id: str, result: Any) -> None:
    """缓存经审批步骤的执行结果（恢复重放去重用）。"""
    try:
        cache.set(f"{SKILL_STEP_CACHE_PREFIX}{step_interrupt_id}", result, SKILL_STEP_CACHE_TTL)
    except Exception:
        # 缓存失败不阻断执行：仅影响重放去重（可能重复执行一次已审批步骤）
        logger.warning(
            f"[StepGate] Skill 步骤结果缓存写入失败（重放可能重复执行）: id={step_interrupt_id}"
        )
