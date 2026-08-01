<script setup>
/**
 * 调试信息面板。
 *
 * 在 ChatMessage 开启 showDebug 时渲染消息的原始字段、Token 用量、
 * 上下文占比、延迟及完整 JSON 数据，方便开发排查问题。
 */
defineProps({
  message: {
    type: Object,
    required: true,
  },
})
</script>

<template>
  <div class="debug-panel">
    <div class="debug-panel-header">
      <span class="debug-panel-title">🐛 调试信息</span>
    </div>
    <div class="debug-panel-body">
      <div class="debug-field">
        <span class="debug-label">消息 ID:</span>
        <span class="debug-value">{{ message.id || 'N/A' }}</span>
      </div>
      <div class="debug-field">
        <span class="debug-label">角色:</span>
        <span class="debug-value">{{ message.role }}</span>
      </div>
      <div class="debug-field">
        <span class="debug-label">时间:</span>
        <span class="debug-value">{{ message.timestamp || 'N/A' }}</span>
      </div>
      <div v-if="message.model" class="debug-field">
        <span class="debug-label">模型:</span>
        <span class="debug-value">{{ message.model }}</span>
      </div>
      <div v-if="message.tokenUsage" class="debug-field">
        <span class="debug-label">Token 用量:</span>
        <span class="debug-value">
          输入 {{ message.tokenUsage.promptTokens || 0 }} / 输出 {{ message.tokenUsage.completionTokens || 0 }} / 总计 {{ message.tokenUsage.totalTokens || 0 }}
        </span>
      </div>
      <div v-if="message.context" class="debug-field">
        <span class="debug-label">上下文:</span>
        <span class="debug-value">{{ message.context.usedTokens || 0 }} / {{ message.context.maxTokens || '128K' }} tokens ({{ ((message.context.percentage || 0) * 100).toFixed(1) }}%)</span>
      </div>
      <div v-if="message.toolCalls && message.toolCalls.length > 0" class="debug-field">
        <span class="debug-label">工具调用:</span>
        <span class="debug-value">{{ message.toolCalls.length }} 次调用</span>
      </div>
      <div v-if="message.sources && message.sources.length > 0" class="debug-field">
        <span class="debug-label">来源数:</span>
        <span class="debug-value">{{ message.sources.length }}</span>
      </div>
      <div v-if="message.latency" class="debug-field">
        <span class="debug-label">延迟:</span>
        <span class="debug-value">{{ message.latency }}ms</span>
      </div>
      <details v-if="message.rawResponse" class="debug-details">
        <summary class="debug-summary">原始响应</summary>
        <pre class="debug-json">{{ typeof message.rawResponse === 'string' ? message.rawResponse : JSON.stringify(message.rawResponse, null, 2) }}</pre>
      </details>
      <details class="debug-details">
        <summary class="debug-summary">完整消息数据</summary>
        <pre class="debug-json">{{ JSON.stringify(message, null, 2) }}</pre>
      </details>
    </div>
  </div>
</template>

<style scoped>
.debug-panel {
  margin-top: 12px;
  border: 1px solid color-mix(in srgb, #f59e0b 30%, transparent);
  border-radius: 8px;
  background: color-mix(in srgb, #f59e0b 4%, var(--card));
  overflow: hidden;
}

.debug-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: color-mix(in srgb, #f59e0b 8%, transparent);
  border-bottom: 1px solid color-mix(in srgb, #f59e0b 15%, transparent);
}

.debug-panel-title {
  font-size: 12px;
  font-weight: 600;
  color: #f59e0b;
}

.debug-panel-body {
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.debug-field {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
}

.debug-label {
  color: var(--muted-foreground);
  white-space: nowrap;
  min-width: 70px;
}

.debug-value {
  color: var(--foreground);
  word-break: break-all;
}

.debug-details {
  margin-top: 4px;
}

.debug-summary {
  font-size: 12px;
  color: #f59e0b;
  cursor: pointer;
  padding: 4px 0;
  user-select: none;
}

.debug-summary:hover {
  text-decoration: underline;
}

.debug-json {
  margin: 4px 0 0;
  padding: 8px;
  background: var(--background);
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 11px;
  font-family: var(--font-mono);
  max-height: 200px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--foreground);
}
</style>
