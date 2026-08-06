"""审批模块 Redis key 常量（单一真相源，Task 10.4）。

集中管理审批流程中所有 Redis key 前缀与 TTL。
历史三处定义（已删除的锁模块死文件、approval_service.py、approval_store.py）
收敛于此单一模块，禁止在其他文件重复定义。

锁常量（SET NX EX 原子获取 / DEL 释放，均带 TTL 防死锁）：
- APPROVAL_LOCK_PREFIX：审批操作锁（防止并发 resume/timeout 同一审批）
- TASK_RESUME_LOCK_PREFIX：任务恢复锁（防止并发恢复同一深度研究任务）
- RESUME_LOCK_PREFIX：深度研究 SETNX 乐观锁（防止并发恢复）
持久化前缀（approval_store 层）：
- APPROVAL_PENDING_PREFIX：pending 列表（按 source_id 索引）
- APPROVAL_PROCESSED_PREFIX：processed 最终状态（按 interrupt_id 索引）
计数前缀：
- PENDING_COUNT_PREFIX：pending 计数（Lua INCR/DECR + TTL）

注意：key 字符串与 TTL 值不得改动，否则 Redis 存量数据对不上。
"""

# 审批操作锁
APPROVAL_LOCK_TTL = 300
APPROVAL_LOCK_PREFIX = "approval:lock:"

# 任务恢复锁
TASK_RESUME_LOCK_PREFIX = "approval:task_resume:"
TASK_RESUME_LOCK_TTL = 1800

# 恢复锁（深度研究 SETNX 乐观锁）
RESUME_LOCK_PREFIX = "approval:resume_lock:"
RESUME_LOCK_TTL = 30

# pending 计数（Lua INCR/DECR）
PENDING_COUNT_PREFIX = "approval:pending_count:"
PENDING_COUNT_TTL = 3600

# Redis key 前缀（按 source_id 索引 pending 列表）
APPROVAL_PENDING_PREFIX = "approval:pending:"

# Redis key 前缀（按 interrupt_id 索引 processed 最终状态）
APPROVAL_PROCESSED_PREFIX = "approval:processed:"

# TTL：30 分钟，与前端 APPROVAL_EXPIRY_MS 一致
APPROVAL_TTL = 1800
