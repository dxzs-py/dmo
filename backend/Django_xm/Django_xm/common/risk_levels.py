"""三级风险分级定义

风险等级是审批体系的基础，用于决定工具调用的审批流程和 UI 展示：
    - SAFE: 自动通过，不 interrupt，仅写入审计日志（auto_approved=True）
    - CONTROLLED: 需用户审批，常规 UI 展示
    - HIGH: 需用户审批，红名高亮 + 强制 Docker 沙箱执行

设计依据：Agent 安全架构 - 沙箱隔离与分级审批方案
三条业务链路（chat 主 Agent / deep_research / 子 Agent）共用同一套风险分级。
"""

from enum import Enum


class RiskLevel(str, Enum):
    """工具调用风险等级。

    继承 str + Enum，支持与字符串字面量直接比较：
        RiskLevel.SAFE == 'safe'  # True
    """

    SAFE = 'safe'
    """自动通过，不 interrupt，仅审计日志（auto_approved=True）"""

    CONTROLLED = 'controlled'
    """需用户审批，常规 UI 展示"""

    HIGH = 'high'
    """需用户审批，红名高亮 + 强制 Docker 沙箱执行"""


# 旧 danger_level（low/medium/high）→ 新 RiskLevel 映射
# 兼容期使用：policies.assess_danger() 仍返回旧格式，assess_risk() 内部转换
_LEGACY_DANGER_TO_RISK: dict[str, RiskLevel] = {
    'low': RiskLevel.SAFE,
    'medium': RiskLevel.CONTROLLED,
    'high': RiskLevel.HIGH,
}


def from_danger_level(danger: str) -> RiskLevel:
    """旧 danger_level 字符串 → RiskLevel 转换。

    Args:
        danger: 旧格式危险等级（low/medium/high）

    Returns:
        对应的 RiskLevel 枚举值；未知值默认 CONTROLLED（安全第一）
    """
    return _LEGACY_DANGER_TO_RISK.get(danger, RiskLevel.CONTROLLED)
