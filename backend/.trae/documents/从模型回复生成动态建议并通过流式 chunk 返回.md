## 目标
- 移除前端写死的建议列表与基于关键词的本地推断
- 由后端在模型完成一次回复后生成 4 条相关建议，作为单独的 SSE 事件返回
- 前端接收并渲染这些建议，点击后直接作为下一次用户输入

## 前端改动
1. 删除硬编码建议
- 移除 `frontend/components/chat/chat-enhanced.tsx#L46-53` 的 `baseSuggestions`
- 移除 `frontend/components/chat/chat-enhanced.tsx#L124-138` 的 `getDynamicSuggestions`

2. 新增建议渲染逻辑
- 在 `chat-enhanced.tsx`：
  - 从最近的助手消息中读取 `metadata.suggestions`（若存在）
  - 在消息区域下方使用已有 `Suggestions`/`Suggestion` 组件渲染这些建议
  - 点击 `Suggestion` 仍复用现有 `handleSuggestionClick(suggestion)` 发送消息

3. Hook 支持新事件
- 在 `frontend/hooks/use-enhanced-chat.ts` 的流处理 `switch(chunk.type)` 中，新增 `case 'suggestions'`：
  - 使用 `MessageManager.getLastAssistantMessage()` 找到最后一条助手消息 id
  - 调用 `manager.setMetadata(id, { suggestions: chunk.data })`

4. 类型扩展（前端）
- 在 `frontend/lib/types.ts` 的 `StreamChunk` 联合类型中新增：
  - `| { type: 'suggestions'; data: string[] }`
- 不引入新的实体字段，统一放在 `EnhancedMessage.metadata.suggestions: string[]`

## 后端改动
1. 生成建议（非阻塞简要逻辑）
- 在 `backend/api/routers/chat.py` 的 `/chat/stream` 流结束前（发送 `context` 和 `end` 之前）
  - 用 `core.models.get_chat_model()` 触发一次补充请求：
    - Prompt：根据“用户问题”和“最终助手回复”，生成 4 条简短、可点击的后续问题建议，返回严格 JSON 数组（字符串）
  - 解析为 `suggestions: List[str]`，异常时降级为空列表
  - 通过 SSE 发送：`data: {"type":"suggestions","data":[...]}\n\n`

2. 保持与现有增强事件兼容
- 不影响现有 `chunk/tool/tool_result/reasoning/source/context/end` 事件
- 建议事件在内容完整后再发送，确保关联的是“本次最终回复”

## 数据契约
- SSE：`{ type: 'suggestions', data: string[] }`
- 前端：将 `data` 写入最后一条助手消息的 `metadata.suggestions`
- UI：仅当存在 `metadata.suggestions` 时渲染建议区域

## 测试与验证
- 手动：
  - 输入基础问题，观察最后渲染的 4 条建议是否与上下文相关
  - 点击建议，确认进入下一轮对话并触发新的建议
- 自动：
  - 在后端为建议生成添加超时/解析容错测试
  - 前端对 `suggestions` chunk 的处理单元测试（mock SSE 数据）

## 边界与降级
- 后端生成失败或解析失败：不发送 `suggestions` 事件，前端不显示建议
- 深度研究模式（长回复）：建议仍生成，但可限制长度与数量，避免 UI 溢出
- 多轮并发：以最后一次助手消息为准写入建议

## 交付说明
- 改动集中在：
  - 前端：`chat-enhanced.tsx`、`hooks/use-enhanced-chat.ts`、`lib/types.ts`
  - 后端：`api/routers/chat.py`
- 不修改 `Suggestions`/`Suggestion` 组件本身，只调整数据来源
