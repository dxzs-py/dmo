<template>
  <div v-if="hasMultipleVersions" class="message-version-selector">
    <div class="version-container">
      <el-button 
        size="small"
        :disabled="currentVersion <= 0" 
        :icon="ArrowLeft"
        @click="goToPrevious"
      />
      <span class="version-label">
        {{ currentVersion + 1 }} of {{ totalVersions }}
      </span>
      <el-button 
        size="small"
        :disabled="currentVersion >= totalVersions - 1" 
        :icon="ArrowRight"
        @click="goToNext"
      />
    </div>
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

.version-container {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 4px 12px;
  background-color: var(--el-fill-color-light);
  border-radius: 6px;
}

.version-label {
  padding: 0 8px;
  font-size: 13px;
  color: var(--el-text-color-regular);
  font-weight: 500;
  min-width: 60px;
  text-align: center;
}
</style>
