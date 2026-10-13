"""跨 app 共享常量定义

将多 app 共用的常量集中定义于此，避免 app 间直接导入导致循环依赖。
常量应只在此处定义，其他模块从此处导入。
"""

from typing import Final

# 审批超时决策标记：作为 Command(resume=...) 的 resume_value，
# 让 ApprovalMiddleware 能区分"用户拒绝"和"审批超时"。
# 取值为字符串 "_timeout"，避免与 True/False 冲突。
TIMEOUT_DECISION = "_timeout"

# 知识库检索类工具名前缀自 research/services/_constants.py
# 下沉：消费方 ai_engine.subagent_runtime.registry.resolve_dedicated_tools 的
# 调用侧（agent_hub.subagent_tools.spawn）用它识别 doc-analyst 专用检索工具。
# 属工具命名约定（机制层），与业务 app 无关。
RETRIEVER_TOOL_NAME_PREFIXES: Final[tuple[str, ...]] = (
    "knowledge_base_",
    "knowledge_bases",
    "knowledge_retrieve",
)
