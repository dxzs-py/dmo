# 项目全面修复报告（RESOLUTION_REPORT）

> 基线参考：`D:\programming\langchain\langchain_xm (93)\langchain_xm`（未重构版本）
> 修复目标：以重构后架构为核心，根因修复重构引入的全部回归问题
> 报告日期：2026-07-30

---

## 一、问题清单总览

| 维度 | 数量 | 状态 |
|------|------|------|
| Signal 机制与事件链路 | 8 | ✅ 全部 resolved |
| 后端功能回归 | 22 | ✅ 全部 resolved |
| 前端架构回归 | 6 | ✅ 全部 resolved |
| 代码质量与安全 | 4 | ✅ 全部 resolved |
| Fundamental 根因问题 | 5 | ✅ 全部 resolved |
| P0 跨浏览器同步 | 4 | ✅ 全部 resolved |
| 新发现问题（测试中发现） | 3 | ✅ 全部 resolved |
| 补丁/注释审查新增 | 2 | ✅ 全部 resolved |
| **合计** | **48** | **48 resolved / 0 open** |

---

## 二、根因分析（按根因分类）

### 根因 1：Signal 注册缺失（影响 6 个 app）

**根因**：重构时所有 app 的 `apps.py ready()` 方法变为空实现，导致 `post_save`/`post_delete` signal handler 未注册。

**影响范围**：
- `SESSION_CREATED` 事件从未发布 → 新建对话非触发浏览器无反应
- `SESSION_DELETED` 事件从未发布 → 删除对话非触发浏览器无反应
- `SESSION_UPDATED` 事件从未发布 → 会话标题更新非触发浏览器无反应
- 消息 CRUD 事件从未发布 → 消息变更非触发浏览器无反应

**修复**：在所有 app 的 `ready()` 中导入 signals 模块；signals.py 使用 `transaction.on_commit` 包装事件发布；`on_session_save` 检测 `is_deleted` 发布 `SESSION_DELETED`（soft_delete 触发 `post_save` 而非 `post_delete`）。

### 根因 2：LangGraph Interrupt ID 身份混淆

**根因**：重构后 `extract_interrupt_ids` 将 `graph_interrupt_id`（ApprovalMiddleware 生成的批次 UUID）直接用作 `langgraph_resume_id`（LangGraph 的 `intr.id`），两者语义不同。

**影响范围**：
- 审批恢复时 `Command(resume=...)` 使用错误的 key → resume 失败
- `ApprovalResumeView` 归属校验使用错误的 ID → "审批请求不属于当前会话"

**修复**：`extract_interrupt_ids` 返回三元组 `(interrupt_value, graph_interrupt_id, langgraph_resume_id)`，优先从 `_meta.graph_interrupt_id` 读取批次 ID，`intr.id` 作为 LangGraph resume key。

### 根因 3：工具调用持久化与 Approval 模型耦合

**根因**：migration 0009 删除了 `ResearchTask.tool_calls` 字段，`_update_tool_call_in_db` 被移除。视图改为从 Approval 模型查询工具调用，丢失自动通过的工具（无 Approval 记录）。

**修复**：
- 后端：恢复 `ResearchTask.tool_calls` 字段 + `_update_tool_call_in_db` 原子持久化 + `_publish_tool_event` 调用 DB 持久化 + 视图从 `tool_calls` 查询 + Approval 左连接
- 前端：`loadHistory` 改用 `deepResearchAPI.getToolCalls`，移除 Approval 迂回路径；`useSnapshotSync` 同步修改；新增 `mapToolCallStateToStatus` 映射

### 根因 4：流式事件未跨浏览器广播

**根因**：`_sync_stream_event_to_session` 仅同步 reasoning/sources/suggestions/context/chunk 事件，工具事件被静默跳过。且 `_broadcast_tool_input_ready` 对空参数工具（`parameters={}`）直接 return，不广播。

**影响范围**：
- 非触发浏览器丢失工具调用事件 → 工具卡片数量不一致（触发 3 个 vs 非触发 1 个）
- 无参数工具（如 `get_current_time`）在非触发浏览器完全不可见

**修复**：
- B1: `_broadcast_tool_input_ready` 仅在 `parameters is None` 时跳过，空 dict `{}` 正常广播
- B2: `tool_call_chunks` 空 args 路径补调 `_broadcast_tool_input_ready`

### 根因 5：审批状态变更未广播

**根因**：`resume_approval` 在审批通过后未调用 `_broadcast_approval_changed_async` 广播 `APPROVAL_APPROVED` 事件。

**修复**：`resume_approval` 调用 `_persist_and_broadcast`，内部通过 `publish_approval_sync` 同步发布审批状态变更事件。

### 根因 6：黑名单命令返回 SAFE 风险等级

**根因**：`ShellExecApprovalPolicy.assess_risk` 中，命中 `BLOCKED_PATTERNS` 的命令（如 `rm -rf /`）返回 `RiskLevel.SAFE`，被审批中间件当作"自动通过"处理。

**影响范围**：危险命令无审批直接执行（依赖工具内部 blocking 作为唯一防线），审计日志显示"自动通过"。

**修复**：黑名单命令返回 `RiskLevel.HIGH`，defense in depth——审批层作为第一道防线让用户显式拒绝，工具内部 blocking 作为第二道防线。

### 根因 7：前端持久化双路径并存

**根因**：后端 `persist_stream_result` 和前端 `syncLastMessageToBackend` 同时更新消息 content，存在竞态。

**修复**：移除前端 PATCH 更新路径，保留 POST 创建消息；后端 `persist_stream_result` 成为 content + tool_calls 持久化的唯一权威。

### 根因 8：流式完成兜底遗漏 APPROVED 状态

**根因**：`_finalizeToolCallsForCompletedMessage` 的 `nonTerminalStatuses` 数组未包含 `APPROVED`，当 `tool_call_completed` 事件丢失时，状态永久卡在 `approved`，UI 显示"已确认"而非"已完成"。

**修复**：将 `ToolCallStatus.APPROVED` 加入 `nonTerminalStatuses`；深度研究完成路径同步修复。

### 根因 9：WS replay 选中最旧会话

**根因**：`handleUserEvent.js` replay 路径在 `currentSessionId` 为空时设为第一条 replay 事件的 session ID，而 replay 按 seq 升序回放（最旧会话先到），导致选中最旧会话。

**修复**：replay 路径不再设置 `currentSessionId`，由 HTTP 路径（按 `updatedAt` 降序选最新）负责。

---

## 三、修复清单（本次会话修复的重点问题）

### issue_audit_15 — 深度研究 tool_calls 与 Approval 解耦

| 层 | 文件 | 修改 |
|----|------|------|
| 后端 Model | `research/models.py` | 恢复 `tool_calls` JSONField |
| 后端 Service | `research/services/research_runner.py` | 添加 `_update_tool_call_in_db` + EventType→DB state 映射（8 个状态含 REJECTED） |
| 后端 Service | `research/services/adapter.py` | `_publish_tool_event` 调用 DB 持久化（非致命） |
| 后端 View | `research/views.py` | `ResearchToolCallsView`/`ResearchSnapshotView` 从 `tool_calls` 查询 + Approval 左连接；提取 `_build_tool_calls_with_approvals`/`_build_approvals_history` |
| 后端 Migration | `0010_researchtask_tool_calls.py` | 恢复 tool_calls 字段 |
| 后端 Migration | `0011_alter_researchtask_status.py` | 修复 status 字段丢失的 CANCELLED 选项 |
| 前端 Type | `types/index.js` | 添加 `mapToolCallStateToStatus` |
| 前端 API | `api/research.js` | 添加 `getToolCalls`/`getSnapshot` |
| 前端 Store | `stores/research.js` | `loadHistory` 改用 `deepResearchAPI.getToolCalls`，移除 `_approvalToToolCall` |
| 前端 Composable | `composables/useSnapshotSync.js` | `createTaskSnapshotSyncInstance` 改用 `deepResearchAPI.getToolCalls` |
| 前端 Test | `stores/__tests__/research.test.js` | mock 从 `getApprovalHistory` 改为 `deepResearchAPI.getToolCalls`，新增自动通过工具测试 |

### issue_p0_tool_approval_whitelist_semantics — 黑名单命令风险等级

| 文件 | 修改 |
|------|------|
| `agent_hub/approval/policies.py` | `ShellExecApprovalPolicy.assess_risk` 黑名单命令返回 `HIGH` 而非 `SAFE` |

### issue_p0_cross_browser_tool_call_count_mismatch — 跨浏览器工具计数

| 文件 | 修改 |
|------|------|
| `chat/services/stream_helpers.py` | B1: `_broadcast_tool_input_ready` 空参数 `{}` 正常广播 |
| `chat/services/stream_helpers.py` | B2: `tool_call_chunks` 空 args 路径补调 `_broadcast_tool_input_ready` |

### issue_new_c — 工具状态卡在"已确认"

| 文件 | 修改 |
|------|------|
| `stores/sync/handleSessionEvent.js` | `_finalizeToolCallsForCompletedMessage` 的 `nonTerminalStatuses` 加入 `APPROVED` |
| `stores/sync/handleSessionEvent.js` | 深度研究完成路径 status 检查加入 `APPROVED` |

### issue_p1_refresh_no_auto_select_latest_session — replay 选中最旧会话

| 文件 | 修改 |
|------|------|
| `stores/sync/handleUserEvent.js` | replay 路径不再设置 `currentSessionId`，由 HTTP 路径负责 |

---

## 四、效果验证

### 后端验证

| 检查项 | 结果 |
|--------|------|
| `manage.py check` | ✅ 0 issues |
| Migration 0010 + 0011 应用 | ✅ OK |
| Django shell 导入验证 | ✅ tool_calls 字段存在，8 个状态映射 |
| 白名单测试（13 tests） | ✅ 全部通过 |
| 工具事件测试（25 tests） | ✅ 全部通过 |

### 前端验证

| 检查项 | 结果 |
|--------|------|
| oxlint（修改文件） | ✅ 0 errors 0 warnings |
| vitest research store（27 tests） | ✅ 全部通过 |
| oxlint（全项目） | ⚠️ 17 pre-existing `no-unused-vars`（死代码，非本次引入） |

### Ontology 问题状态

| 状态 | 数量 |
|------|------|
| resolved | 48 |
| open | 0 |

---

## 五、审计文档索引

| 文档 | 内容 |
|------|------|
| `memory/audit/01_signal_and_event_chain.md` | Signal 与事件链路审计 |
| `memory/audit/02_backend_functional_regression.md` | 后端功能回归审计（22 个问题） |
| `memory/audit/03_signal_and_event_chain_full_audit.md` | Signal 机制全量审计（17 个问题） |
| `memory/audit/04_frontend_and_quality_audit.md` | 前端架构与代码质量审计 |
| `memory/audit/05_patch_review.md` | 补丁式修复审查（2 个迂回兼容已根本修复） |
| `memory/audit/06_comment_review.md` | 注释合理性审查（28 个问题已修正） |
| `memory/ontology/graph.jsonl` | Ontology 问题图谱（48 Issue + Action 实体） |

---

## 六、后续建议

1. ~~**死代码清理**：前端有 17 个 `no-unused-vars` 错误（未使用导入/变量），建议统一清理~~ **已完成（2026-07-31）**：oxlint 全项目 0 errors 0 warnings，修复 5 处问题：
   - `message-operations.test.js`：修复重复 `tool_call_id` key + 错误测试名
   - `session-transformers.js`：移除未使用 `settings` 导入
   - `tool-adapters.test.js`：移除未使用 `beforeEach`/`ToolCallStatus` 导入
   - `message-operations.js`：移除 `_matchPendingApprovals` 未使用的 `data` 参数（根本修复，非下划线前缀）
   - `useStreamFinalizer.test.js`：同步 issue_new_b 修复，移除过时的 `syncLastMessageToBackend` 断言
   - `useStreamFinalizer.js`：修复 step 编号不连续（3→5 跳号，issue_new_b 移除 step 4 后未更新编号）
2. **集成测试**：本次修复涉及跨浏览器同步、工具审批、深度研究等核心链路，建议执行 4 浏览器 MCP 集成测试验证
3. **深度研究 tool_calls 迁移**：已有历史 ResearchTask 记录的 `tool_calls` 字段为空 `{}`，新任务会自动填充；历史记录可通过重新触发研究或从 checkpoint 重建
