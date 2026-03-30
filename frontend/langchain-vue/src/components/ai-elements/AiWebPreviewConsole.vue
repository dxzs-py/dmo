<template>
  <el-collapse v-model="localConsoleOpen" class="ai-web-preview-console" :class="className">
    <el-collapse-item title="Console" name="console">
      <div class="console-content">
        <div v-if="logs.length === 0" class="no-logs">
          No console output
        </div>
        <div v-else class="logs-list">
          <div
            v-for="(log, index) in logs"
            :key="`${log.timestamp.getTime()}-${index}`"
            :class="['log-item', log.level]"
          >
            <span class="log-time">{{ log.timestamp.toLocaleTimeString() }}</span>
            <span class="log-message">{{ log.message }}</span>
          </div>
        </div>
        <slot></slot>
      </div>
    </el-collapse-item>
  </el-collapse>
</template>

<script setup>
import { ref, inject, watch } from 'vue'

defineProps({
  className: {
    type: String,
    default: ''
  },
  logs: {
    type: Array,
    default: () => []
  }
})

const webPreview = inject('webPreview')
const localConsoleOpen = ref(webPreview?.consoleOpen.value ? ['console'] : [])

watch(localConsoleOpen, (newVal) => {
  if (webPreview) {
    webPreview.setConsoleOpen(newVal.includes('console'))
  }
})

if (webPreview) {
  watch(() => webPreview.consoleOpen.value, (newVal) => {
    localConsoleOpen.value = newVal ? ['console'] : []
  })
}
</script>

<style scoped>
.ai-web-preview-console {
  border-top: 1px solid var(--el-border-color);
  background-color: var(--el-fill-color-lighter);
  font-family: monospace;
  font-size: 14px;
}

.console-content {
  max-height: 192px;
  overflow-y: auto;
}

.no-logs {
  color: var(--el-text-color-secondary);
}

.logs-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.log-item {
  font-size: 12px;
}

.log-item.error {
  color: var(--el-color-danger);
}

.log-item.warn {
  color: var(--el-color-warning);
}

.log-item.log {
  color: var(--el-text-color-primary);
}

.log-time {
  color: var(--el-text-color-secondary);
  margin-right: 8px;
}
</style>
