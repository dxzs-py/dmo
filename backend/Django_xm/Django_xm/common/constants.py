"""跨 app 共享常量定义

将多 app 共用的常量集中定义于此，避免 app 间直接导入导致循环依赖。
常量应只在此处定义，其他模块从此处导入。
"""

# 审批超时决策标记：作为 Command(resume=...) 的 resume_value，
# 让 ApprovalMiddleware 能区分"用户拒绝"和"审批超时"。
# 取值为字符串 "_timeout"，避免与 True/False 冲突。
TIMEOUT_DECISION = "_timeout"
