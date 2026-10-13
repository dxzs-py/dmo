"""沙箱共享常量（跨 app 单一来源）。

SANDBOX_ALLOWED_DIRS 原权威源为 ``apps/research/services/_constants.py``；
spec 任务 4.3 环 3 破除时下沉 common 层（消费方 agent_hub.builders.deep_builder
与 research 均为业务层，沙箱目录布局属机制层约定，与 app 无关）。
历史上曾在 deep_builder 与 adapter 两处定义不一致（漂移 bug），单源化防复发。
"""

from __future__ import annotations

from typing import Final

# Agent 工具区允许写入的子目录
# /sandbox/ 是 Agent 工具区（skills、MCP 工具、第三方依赖等），
# 只允许预定义的工具子目录写入，其他 /sandbox/ 路径一律拒绝，
# 引导 Agent 将研究产出写入 /notes/、/plans/、/reports/。
SANDBOX_ALLOWED_DIRS: Final[tuple[str, ...]] = (
    "/sandbox/skills/",
    "/sandbox/mcp/",
    "/sandbox/deps/",
    "/sandbox/tmp/",
    "/sandbox/artifacts/",  # 工具结果缓存（系统自动管理）
    "/sandbox/inherited/",  # 续研时继承的父任务文件，供 agent 读取参考
)
