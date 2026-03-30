<template>
  <div v-if="hasMultipleVersions" class="message-version-selector">
    <el-button-group size="small">
      <el-button 
        :disabled="!hasMultipleVersions" 
        @click="goToPrevious"
        :icon="ArrowLeft"
      />
      <span class="version-label">
        {{ currentVersion + 1 }} of {{ totalVersions }}
      </span>
      <el-button 
        :disabled="!hasMultipleVersions" 
        @click="goToNext"
        :icon="ArrowRight"
      />
    </el-button-group>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { ArrowLeft, ArrowRight } from '@element-plus/icons-vue'
import { useSessionStore } from '../../stores/session'

const props = defineProps({
  message: {
    type: Object,
    required: true
  },
  messageIndex: {
    type: Number,
    required: true
  }
})

const sessionStore = useSessionStore()

const hasMultipleVersions = computed(() => {
  return props.message.versions && props.message.versions.length > 1
})

const totalVersions = computed(() => {
  return props.message.versions ? props.message.versions.length : 1
})

const currentVersion = computed(() => {
  return props.message.currentVersion !== undefined ? props.message.currentVersion : 0
})

const goToPrevious = () => {
  if (!hasMultipleVersions.value) return
  const newVersion = currentVersion.value > 0 
    ? currentVersion.value - 1 
    : totalVersions.value - 1
  sessionStore.switchMessageVersion(sessionStore.currentSessionId, props.messageIndex, newVersion)
}

const goToNext = () => {
  if (!hasMultipleVersions.value) return
  const newVersion = currentVersion.value < totalVersions.value - 1 
    ? currentVersion.value + 1 
    : 0
  sessionStore.switchMessageVersion(sessionStore.currentSessionId, props.messageIndex, newVersion)
}
</script>

<style scoped>
.message-version-selector {
  display: flex;
  justify-content: center;
  margin-top: 8px;
  margin-bottom: 8px;
}

.version-label {
  padding: 0 12px;
  display: flex;
  align-items: center;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  background-color: var(--el-bg-color);
  border-top: 1px solid var(--el-border-color);
  border-bottom: 1px solid var(--el-border-color);
}
</style>
